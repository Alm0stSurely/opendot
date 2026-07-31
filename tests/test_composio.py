"""Tests for the Composio integration's local logic — config, identity,
namespacing, and fail-soft behavior. Network calls (list_apps, execute) are not
tested here (they need a real key); we verify they don't raise on bad keys.
"""

import os
import stat

import pytest

from opendot.tools import composio as cx


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))


def test_unconfigured_then_configured():
    assert cx.is_configured() is False
    uid = cx.set_api_key("comp_testkey")
    assert uid.startswith("opendot-")
    assert cx.is_configured() is True
    # user_id is stable across reads
    assert cx.load_config()["user_id"] == uid


def test_api_key_file_is_private(tmp_path):
    cx.set_api_key("comp_secret")
    path = cx._config_path()
    mode = stat.S_IMODE(path.stat().st_mode)
    # owner-only (0o600); skip the assertion on platforms that don't honor it
    if os.name != "nt":
        assert mode == 0o600


def test_enable_apps_dedup_and_persist():
    cx.set_api_key("k")
    cx.add_enabled_app("gmail")
    cx.add_enabled_app("slack")
    cx.add_enabled_app("gmail")  # dup
    assert cx.enabled_apps() == ["gmail", "slack"]


def test_disable_app_removes_locally_and_invalidates_session():
    # With a fake key the revoke network call fails soft (returns 0) but the app
    # is still removed from the enabled list and the session cache is cleared.
    cx.set_api_key("k")
    cx.add_enabled_app("gmail")
    cx._SESSION = object()  # pretend a session was cached
    revoked = cx.disable_app("gmail")
    assert "gmail" not in cx.enabled_apps()
    assert revoked == 0  # fake key → no real deletion
    assert cx._SESSION is None  # cache invalidated

    # revoke=False skips the network call entirely
    cx.add_enabled_app("slack")
    assert cx.disable_app("slack", revoke=False) == 0
    assert "slack" not in cx.enabled_apps()


def test_verify_api_key_accepts_working_key_and_rejects_bad(monkeypatch):
    # A key is "valid" only if an authenticated call actually succeeds — a bad
    # key must be rejected up front, not stored and failed on later.
    import composio

    class _Toolkits:
        def __init__(self, ok):
            self._ok = ok

        def list(self, **_):
            if not self._ok:
                raise RuntimeError("401 Unauthorized")
            return object()

    class _FakeComposio:
        def __init__(self, api_key=None):
            # the fixture routes "good"/"bad" via the key string itself
            self.toolkits = _Toolkits(api_key == "good")

    monkeypatch.setattr(composio, "Composio", _FakeComposio)
    assert cx.verify_api_key("good") is True
    assert cx.verify_api_key("bad") is False


def test_friendly_error_explains_read_only_key():
    # The exact 403 a read-only Composio key produces on connect.
    raw = RuntimeError(
        "PermissionDenied: 403 - {'error': {'message': 'This API key does not "
        "have the permissions required for POST /api/v3/connected_accounts...', "
        "'slug': 'APIKey_InsufficientPermissions', "
        "'suggested_fix': 'Grant the connected_accounts permission with write.'}}"
    )
    msg = cx._friendly_error(raw)
    assert "read-only" in msg
    assert "write access" in msg
    assert "{" not in msg  # no raw JSON blob leaking through


def test_friendly_error_surfaces_suggested_fix_for_other_errors():
    raw = RuntimeError("400 - {'error': {'message': 'bad', 'suggested_fix': 'do the thing'}}")
    assert cx._friendly_error(raw) == "do the thing"


def test_namespacing_helpers():
    assert cx.is_composio_tool("composio__gmail__GMAIL_SEND_EMAIL")
    assert not cx.is_composio_tool("read_file")
    assert not cx.is_composio_tool("mcp__github__create_issue")


def test_execute_without_session_is_soft_error():
    # Configured but no enabled apps → no Tool Router session → soft error,
    # never a crash.
    cx.set_api_key("k")
    out = cx.execute_tool("composio__COMPOSIO_SEARCH_TOOLS", {})
    assert out.startswith("error:")


def test_build_specs_empty_without_enabled_apps():
    cx.set_api_key("k")  # configured but no apps enabled
    assert cx.build_tool_specs() == []


def test_looks_like_auth_error_distinguishes_auth_from_generic():
    assert cx.looks_like_auth_error('{"error": "Gmail not connected"}')
    assert cx.looks_like_auth_error("401 Unauthorized")
    assert cx.looks_like_auth_error("please connect your account")
    # generic failures are NOT auth errors
    assert not cx.looks_like_auth_error("rate limit exceeded")
    assert not cx.looks_like_auth_error('{"error": "invalid argument: to"}')
