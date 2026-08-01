"""Tests for browser-OAuth support for authenticated MCP servers (issue #35).

Covers the storage layer (token roundtrip + permissions), the loopback callback
server, the provider factory, config/spec handling (auth:oauth), and the CLI
--oauth flag. The actual browser dance is not exercised (no real server), but the
pieces that would drive it are.
"""

import json
import sys
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    yield


# -- FileTokenStorage --------------------------------------------------------


async def test_token_storage_roundtrip():
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    from opendot.mcp.oauth import FileTokenStorage, has_stored_tokens

    store = FileTokenStorage("linear")
    assert await store.get_tokens() is None
    assert not has_stored_tokens("linear")

    tok = OAuthToken(access_token="abc123", token_type="Bearer", refresh_token="r1")
    await store.set_tokens(tok)
    got = await store.get_tokens()
    assert got is not None
    assert got.access_token == "abc123"
    assert got.refresh_token == "r1"
    assert has_stored_tokens("linear")

    info = OAuthClientInformationFull(
        client_id="cid", redirect_uris=["http://127.0.0.1:9/callback"]
    )
    await store.set_client_info(info)
    got_info = await store.get_client_info()
    assert got_info is not None
    assert got_info.client_id == "cid"
    # tokens survive a client_info write (same file, merged)
    assert (await store.get_tokens()).access_token == "abc123"


async def test_token_file_is_owner_only():
    from mcp.shared.auth import OAuthToken

    from opendot.mcp.oauth import FileTokenStorage, _oauth_dir, _token_file

    store = FileTokenStorage("secretsrv")
    await store.set_tokens(OAuthToken(access_token="x", token_type="Bearer"))
    path = _token_file("secretsrv")
    assert path.exists()
    # tokens are secrets — file must not be group/world readable
    assert (path.stat().st_mode & 0o077) == 0, "token file must be mode 0600"

    # An overwrite (second write, e.g. token refresh) must preserve 0600 and not
    # leave a temp file behind (atomic write via os.replace).
    await store.set_tokens(OAuthToken(access_token="y", token_type="Bearer"))
    assert (path.stat().st_mode & 0o077) == 0
    assert not any(p.name.endswith(".tmp") for p in _oauth_dir().iterdir())


def test_clear_tokens():
    from opendot.mcp.oauth import _token_file, clear_tokens

    p = _token_file("gone")
    p.write_text(json.dumps({"tokens": {"access_token": "z", "token_type": "Bearer"}}))
    assert clear_tokens("gone") is True
    assert not p.exists()
    assert clear_tokens("gone") is False  # already absent


def test_token_filename_is_sanitized():
    from opendot.mcp.oauth import _token_file

    # a server name with path separators must not escape the oauth dir
    p = _token_file("../../etc/passwd")
    assert "oauth" in str(p.parent)
    assert "/" not in p.name.replace(".json", "")


# -- loopback callback server ------------------------------------------------


def test_loopback_callback_captures_code_and_state():
    from opendot.mcp.oauth import _LoopbackCallbackServer

    cb = _LoopbackCallbackServer()
    cb.start()
    try:
        uri = cb.redirect_uri
        assert uri.startswith("http://127.0.0.1:")
        # simulate the provider's browser redirect back to us
        urllib.request.urlopen(uri + "?code=THECODE&state=xyz", timeout=5).read()
        result = cb.wait(5)
        assert result["code"] == "THECODE"
        assert result["state"] == "xyz"
        assert result["error"] is None
    finally:
        cb.close()


def test_loopback_callback_reports_error_param():
    from opendot.mcp.oauth import _LoopbackCallbackServer

    cb = _LoopbackCallbackServer()
    cb.start()
    try:
        urllib.request.urlopen(cb.redirect_uri + "?error=access_denied", timeout=5).read()
        result = cb.wait(5)
        assert result["error"] == "access_denied"
        assert result["code"] is None
    finally:
        cb.close()


def test_loopback_callback_escapes_error_html():
    # A crafted error value must not inject markup into the page we serve back.
    from opendot.mcp.oauth import _LoopbackCallbackServer

    cb = _LoopbackCallbackServer()
    cb.start()
    try:
        # ?error=<script>alert(1)</script> (URL-encoded)
        payload = "%3Cscript%3Ealert(1)%3C%2Fscript%3E"
        page = urllib.request.urlopen(cb.redirect_uri + f"?error={payload}", timeout=5).read()
        page = page.decode()
        assert "<script>alert(1)</script>" not in page  # raw markup must not appear
        assert "&lt;script&gt;" in page  # it's escaped instead
    finally:
        cb.close()


# -- provider factory --------------------------------------------------------


def test_build_oauth_provider_returns_auth_flow():
    from opendot.mcp.oauth import build_oauth_provider

    provider, cb = build_oauth_provider("https://mcp.linear.app/mcp", "linear")
    try:
        # The provider drives auth via the httpx auth_flow protocol (a generator of
        # requests). That's the contract the transport's auth= param consumes — it
        # subclasses the SDK's Auth, which mirrors httpx.Auth, so a plain
        # isinstance(httpx.Auth) check doesn't hold; presence of auth_flow is the
        # real requirement.
        assert callable(getattr(provider, "auth_flow", None))
        # its redirect_uri points at our loopback callback server
        assert provider.context.client_metadata.redirect_uris[0].host == "127.0.0.1"
    finally:
        cb.close()


# -- config / spec handling --------------------------------------------------


async def test_remove_oauth_server_clears_tokens():
    from mcp.shared.auth import OAuthToken

    from opendot.mcp.manager import add_mcp_server, remove_mcp_server
    from opendot.mcp.oauth import FileTokenStorage, has_stored_tokens

    add_mcp_server("linear", {"url": "https://mcp.linear.app/mcp", "auth": "oauth"})
    await FileTokenStorage("linear").set_tokens(OAuthToken(access_token="t", token_type="Bearer"))
    assert has_stored_tokens("linear")

    assert remove_mcp_server("linear") is True
    assert not has_stored_tokens("linear")  # tokens forgotten on removal


def test_shutdown_closes_leftover_callback_servers():
    # If start() times out mid-authorization, a callback server can still be open.
    # shutdown() must close it rather than leak the loopback thread.
    from opendot.mcp.manager import MCPManager
    from opendot.mcp.oauth import _LoopbackCallbackServer

    mgr = MCPManager({})  # empty config: start() is a no-op, no background loop
    cb = _LoopbackCallbackServer()
    cb.start()
    mgr._callbacks.append(cb)

    mgr.shutdown()  # should sweep the callback even with no loop running
    assert mgr._callbacks == []
    # the loopback port is closed: binding is released (a second bind would work),
    # and a follow-up close is a harmless no-op (idempotent).
    mgr.shutdown()


def test_cli_add_oauth_flag_stores_auth_oauth(monkeypatch):
    # Don't actually run the browser flow: stub authorize_oauth_server.
    import opendot.mcp as mcp_pkg
    from opendot import cli
    from opendot.mcp.manager import load_mcp_config

    def _fake_authorize(name, spec):
        from opendot.mcp.manager import AuthorizeResult

        return AuthorizeResult(ok=True, tool_count=3)

    monkeypatch.setattr(mcp_pkg, "authorize_oauth_server", _fake_authorize)
    monkeypatch.setattr(
        sys,
        "argv",
        ["opendot", "mcp", "add", "linear", "--url", "https://mcp.linear.app/mcp", "--oauth"],
    )
    cli.main()
    cfg = load_mcp_config()
    assert cfg["linear"] == {"url": "https://mcp.linear.app/mcp", "auth": "oauth"}


def test_cli_add_oauth_ignores_static_header(monkeypatch):
    # --oauth wins: a stray --header is not stored (the provider supplies auth).
    import opendot.mcp as mcp_pkg
    from opendot import cli
    from opendot.mcp.manager import load_mcp_config

    monkeypatch.setattr(
        mcp_pkg,
        "authorize_oauth_server",
        lambda n, s: __import__(
            "opendot.mcp.manager", fromlist=["AuthorizeResult"]
        ).AuthorizeResult(ok=True, tool_count=0),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "opendot",
            "mcp",
            "add",
            "srv",
            "--url",
            "https://x/mcp",
            "--oauth",
            "--header",
            "Authorization=Bearer nope",
        ],
    )
    cli.main()
    spec = load_mcp_config()["srv"]
    assert spec == {"url": "https://x/mcp", "auth": "oauth"}
    assert "headers" not in spec
