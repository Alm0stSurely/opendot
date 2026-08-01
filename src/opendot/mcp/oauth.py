"""Browser-OAuth support for authenticated remote MCP servers (issue #35).

Many hosted MCP servers (Linear, Notion, GitHub's remote MCP, …) don't hand out a
static token you can paste into an ``Authorization`` header — they use OAuth 2.0:
you authorize in a browser, the server issues short-lived tokens, and the client
refreshes them. This module wires the MCP SDK's ``OAuthClientProvider`` into
opendot so a server marked ``"auth": "oauth"`` gets that flow.

Three pieces, mirroring the desktop-OAuth pattern opendot already uses for Composio:

* ``FileTokenStorage`` — persists the registered OAuth client + issued tokens to
  ``~/.opendot/mcp_oauth/<server>.json`` (mode 0600; these are credentials), so a
  server authorized once stays authorized across launches until the token is revoked.
* ``_LoopbackCallbackServer`` — a one-shot ``127.0.0.1:<port>`` HTTP server that
  catches the ``?code=…&state=…`` redirect and shows a "you can close this tab" page.
  Its address is the OAuth ``redirect_uri``.
* ``build_oauth_provider`` — assembles an ``OAuthClientProvider`` (an ``httpx.Auth``)
  that the transport layer passes as ``auth=`` to the streamable-http / SSE client.

Tokens live outside the tracked config (``mcp.json`` stays credential-free); the
config only records ``"auth": "oauth"`` so opendot knows to attach the provider.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from opendot.reversibility.snapshots import store_root

# opendot registers itself as this OAuth client (dynamic registration fills in the
# rest). client_name shows on the provider's consent screen.
_CLIENT_NAME = "opendot"
_DEFAULT_SCOPE = None  # let the server decide; some reject unknown scopes


def _oauth_dir() -> Path:
    d = store_root() / "mcp_oauth"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token_file(server: str) -> Path:
    # server names come from the user's own config; keep the filename filesystem-safe.
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in server)
    return _oauth_dir() / f"{safe}.json"


class FileTokenStorage:
    """SDK ``TokenStorage`` backed by one JSON file per server.

    Stores both the OAuth tokens and the dynamically-registered client info so we
    don't re-register on every launch. Written with owner-only permissions.
    """

    def __init__(self, server: str) -> None:
        self._path = _token_file(server)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file just means "re-auth"
            return {}

    def _write(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            self._path.chmod(0o600)  # tokens are secrets
        except OSError:
            pass

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


def has_stored_tokens(server: str) -> bool:
    """True if we already hold an OAuth token for this server (authorized before)."""
    try:
        return bool(json.loads(_token_file(server).read_text(encoding="utf-8")).get("tokens"))
    except Exception:  # noqa: BLE001
        return False


def clear_tokens(server: str) -> bool:
    """Forget a server's OAuth tokens/registration (used when removing it). Returns
    True if anything was deleted."""
    path = _token_file(server)
    if path.exists():
        path.unlink()
        return True
    return False


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the single OAuth redirect and answers with a close-me page."""

    # set by the server instance
    result: dict = {}
    done: threading.Event

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        params = parse_qs(urlparse(self.path).query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]
        self.server.result = {"code": code, "state": state, "error": error}  # type: ignore[attr-defined]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if error:
            # Escape: this value comes from the redirect URL, so a crafted link
            # (e.g. ?error=<script>…) must not inject markup into the page we serve.
            safe = escape(error)
            body = f"<h2>Authorization failed</h2><p>{safe}</p><p>You can close this tab.</p>"
        else:
            body = "<h2>Authorized ✓</h2><p>You can close this tab and return to opendot.</p>"
        self.wfile.write(f"<!doctype html><html><body>{body}</body></html>".encode())
        self.server.done.set()  # type: ignore[attr-defined]

    def log_message(self, *args) -> None:  # silence stdlib request logging
        return


class _LoopbackCallbackServer:
    """One-shot loopback HTTP server that yields the OAuth redirect's code+state.

    Bind to an ephemeral port on 127.0.0.1; ``redirect_uri`` is this address. The
    server runs on its own thread so the async callback handler can block-wait on it.
    """

    def __init__(self) -> None:
        self._httpd = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._httpd.result = {}  # type: ignore[attr-defined]
        self._httpd.done = threading.Event()  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://127.0.0.1:{port}/callback"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float) -> dict:
        """Block until the redirect arrives (or timeout). Returns {code,state,error}."""
        got = self._httpd.done.wait(timeout)  # type: ignore[attr-defined]
        if not got:
            return {"code": None, "state": None, "error": "timeout waiting for authorization"}
        return dict(self._httpd.result)  # type: ignore[attr-defined]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def build_oauth_provider(server_url: str, server_name: str):
    """Build an ``OAuthClientProvider`` for a server, plus its callback server.

    Returns ``(provider, callback_server)``. The caller passes ``provider`` as
    ``auth=`` to the transport and must ``callback_server.close()`` when the
    connection attempt is done (win or lose). The provider drives the flow: on the
    first (unauthenticated) request it opens the browser via ``redirect_handler``
    and waits for ``callback_handler`` to return the code; thereafter it uses the
    stored/refreshed tokens with no browser interaction.
    """
    import anyio
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    callback = _LoopbackCallbackServer()
    callback.start()

    metadata = OAuthClientMetadata(
        client_name=_CLIENT_NAME,
        redirect_uris=[callback.redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=_DEFAULT_SCOPE,
        token_endpoint_auth_method="none",  # public client (PKCE), no client secret
    )

    async def redirect_handler(auth_url: str) -> None:
        # Open the user's browser to the provider's consent screen.
        webbrowser.open(auth_url)

    async def callback_handler() -> tuple[str, str | None]:
        # Wait (off the event loop) for the loopback server to catch the redirect.
        result = await anyio.to_thread.run_sync(lambda: callback.wait(300))
        if result.get("error") or not result.get("code"):
            raise RuntimeError(result.get("error") or "no authorization code received")
        return result["code"], result.get("state")

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=FileTokenStorage(server_name),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    return provider, callback
