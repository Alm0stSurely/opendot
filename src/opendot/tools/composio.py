"""Composio integration — connect opendot to 3000+ app tools (Gmail, Slack,
GitHub, Notion, …) using the user's own Composio API key.

Design mirrors how Plandot uses Composio, adapted for a local terminal app:

* **Identity.** Composio scopes everything to a ``user_id``. opendot has no
  accounts, so on first use we mint a stable local id and store it (with the
  api key) in ``~/.opendot/composio.json``. All connections belong to it.

* **Auth.** ``toolkits.authorize(user_id, toolkit)`` returns a ConnectionRequest.
  For OAuth apps it carries a ``redirect_url`` — we open it in the user's
  browser and block on ``wait_for_connection`` until the account goes ACTIVE.
  For no-auth / API-key connectors there's no redirect and it activates
  immediately.

* **Tools.** ``tools.get(user_id, toolkits=[slug])`` returns tool schemas;
  ``tools.execute(slug, arguments, user_id=...)`` runs one. Every Composio call
  reaches an external service, so — exactly like MCP tools — opendot treats it
  as **irreversible**: confirmed before running and marked ✗ in the ledger.

The composio SDK is a core dependency, but everything here still fails soft if
the import is unavailable, so a broken install never crashes opendot.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from opendot.reversibility.snapshots import store_root


def _config_path():
    return store_root() / "composio.json"


def load_config() -> dict:
    """Read ~/.opendot/composio.json → {"api_key", "user_id", "apps": [...]}."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:  # keep the key file private (best-effort; no-op on Windows)
        path.chmod(0o600)
    except OSError:
        pass


def is_configured() -> bool:
    return bool(load_config().get("api_key"))


def verify_api_key(api_key: str) -> bool:
    """True if the key actually works (a cheap authenticated call succeeds).
    Lets the caller reject a bad/old key up front instead of storing it and
    only failing later when apps won't load."""
    from composio import Composio

    try:
        Composio(api_key=api_key.strip()).toolkits.list(limit=1)
        return True
    except Exception:  # noqa: BLE001 - any failure (auth, network) = don't trust it
        return False


def set_api_key(api_key: str) -> str:
    """Store the api key, minting a stable local user_id on first use.
    Returns the user_id."""
    cfg = load_config()
    cfg["api_key"] = api_key.strip()
    if not cfg.get("user_id"):
        cfg["user_id"] = "opendot-" + uuid.uuid4().hex[:12]
    save_config(cfg)
    return cfg["user_id"]


def enabled_apps() -> list[str]:
    """Toolkit slugs the user has activated in opendot (their tools load on launch)."""
    return list(load_config().get("apps", []))


def add_enabled_app(slug: str) -> None:
    cfg = load_config()
    apps = cfg.get("apps", [])
    if slug not in apps:
        apps.append(slug)
    cfg["apps"] = apps
    save_config(cfg)


def disable_app(slug: str, *, revoke: bool = True) -> int:
    """Remove an app from the enabled list so its tools stop loading. By default
    also **revokes** the Composio-side connection(s) for that toolkit, so a later
    reconnect works cleanly (otherwise Composio accumulates duplicate accounts
    and reconnect fails with ComposioMultipleConnectedAccountsError).

    Returns the number of Composio connections deleted (0 if none/failure)."""
    cfg = load_config()
    cfg["apps"] = [s for s in cfg.get("apps", []) if s != slug]
    save_config(cfg)
    # Invalidate the cached Tool Router session (its toolkit set changed).
    global _SESSION, _SESSION_APPS
    _SESSION, _SESSION_APPS = None, ()
    if not revoke:
        return 0
    return _revoke_connections(slug)


def _revoke_connections(slug: str) -> int:
    """Delete all Composio connected accounts for a toolkit. Returns the count
    deleted. Never raises."""
    try:
        client, user_id = _client()
        resp = client.connected_accounts.list(user_ids=[user_id], toolkit_slugs=[slug])
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for acc in getattr(resp, "items", None) or []:
        acc_id = getattr(acc, "id", None) or getattr(acc, "nanoid", None)
        if not acc_id:
            continue
        try:
            client.connected_accounts.delete(acc_id)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def composio_available() -> bool:
    try:
        import composio  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _client():
    """A Composio client bound to the stored api key. Raises if unconfigured."""
    from composio import Composio

    cfg = load_config()
    key = cfg.get("api_key")
    if not key:
        raise RuntimeError("Composio is not configured (no api key). Run /composio.")
    return Composio(api_key=key), cfg["user_id"]


# ---- catalog / connection management (used by the /composio TUI flow) ----


def list_apps(search: str | None = None, limit: int = 200) -> list[dict]:
    """Available Composio toolkits: [{slug, name, description}]. Empty on failure."""
    try:
        client, _ = _client()
        resp = client.toolkits.list(limit=limit, sort_by="usage")
    except Exception:  # noqa: BLE001
        return []
    items = getattr(resp, "items", None) or []
    out = []
    for tk in items:
        slug = (getattr(tk, "slug", "") or "").lower()
        if not slug:
            continue
        meta = getattr(tk, "meta", None)
        desc = getattr(tk, "description", "") or (getattr(meta, "description", "") if meta else "")
        name = getattr(tk, "name", "") or slug
        if search and search.lower() not in f"{name} {slug}".lower():
            continue
        out.append({"slug": slug, "name": name, "description": desc})
    return out


def list_connected() -> set[str]:
    """Toolkit slugs the user has an ACTIVE connection for."""
    try:
        client, user_id = _client()
        resp = client.connected_accounts.list(user_ids=[user_id], statuses=["ACTIVE"])
    except Exception:  # noqa: BLE001
        return set()
    items = getattr(resp, "items", None) or []
    slugs = set()
    for acc in items:
        tk = getattr(acc, "toolkit", None)
        slug = getattr(tk, "slug", None) if tk else None
        if slug:
            slugs.add(slug.lower())
    return slugs


@dataclass
class ConnectResult:
    ok: bool
    needs_auth: bool = False
    redirect_url: str | None = None
    request: Any = None  # the ConnectionRequest, so the caller can wait on it
    error: str | None = None


def _friendly_error(exc: Exception) -> str:
    """Turn a Composio SDK exception into a short, actionable message. Composio
    puts a human 'message' and a 'suggested_fix' in the error body — surface
    those instead of dumping the raw 403/JSON at the user."""
    text = str(exc)
    # A read-only key is the common one: it authenticates (apps list fine) but
    # can't create connections. Give the exact fix, not the JSON blob.
    if "InsufficientPermissions" in text or "write access" in text:
        return (
            "your Composio API key is read-only — it can't connect apps. "
            "Create a key with 'connected_accounts' write access in the "
            "Composio dashboard, then run /composio to re-enter it."
        )
    # Prefer the actionable 'suggested_fix'; fall back to the human 'message'.
    for field in ("suggested_fix", "message"):
        m = re.search(rf"'{field}':\s*'([^']+)'", text)
        if m:
            return m.group(1)
    return f"{type(exc).__name__}: {exc}"


def begin_connect(slug: str) -> ConnectResult:
    """Start connecting a toolkit. If it needs OAuth, returns the redirect_url and
    the ConnectionRequest (caller opens the browser then calls
    ``wait_for_connection``). If it's a direct/no-auth connector — or already
    connected — it's active and ok=True."""
    try:
        client, user_id = _client()
    except Exception as exc:  # noqa: BLE001
        return ConnectResult(ok=False, error=_friendly_error(exc))
    # Already connected on Composio's side → reuse it, don't re-authorize (which
    # errors with ComposioMultipleConnectedAccountsError once there's ≥1).
    if slug in list_connected():
        return ConnectResult(ok=True)
    try:
        req = client.toolkits.authorize(user_id=user_id, toolkit=slug)
    except Exception as exc:  # noqa: BLE001
        return ConnectResult(ok=False, error=_friendly_error(exc))
    redirect = getattr(req, "redirect_url", None)
    if redirect:
        return ConnectResult(ok=False, needs_auth=True, redirect_url=redirect, request=req)
    return ConnectResult(ok=True, request=req)


# ---- tool specs + execution (used by the Toolbox) ----

# --- Tool Router session (the scalable tool surface) -----------------------
#
# A Composio app can expose hundreds of tools; binding them all blows past the
# model's tools-array cap (OpenAI: 128). So — like Plandot — we use Composio's
# **Tool Router**: `composio.create(user_id, toolkits=[...])` returns a session
# whose `.tools()` is a small, bounded set of meta-tools (search / get-schema /
# execute). The agent discovers the specific tool it needs at runtime via those,
# and we route calls through `session.execute()`. No cap, nothing truncated.

_SESSION = None  # cached ToolRouterSession
_SESSION_APPS: tuple = ()  # the enabled-apps set the cached session was built for


def _session():
    """Return a Tool Router session scoped to the enabled apps, cached until the
    enabled-app set changes. None if composio is unconfigured/unavailable."""
    global _SESSION, _SESSION_APPS
    apps = tuple(enabled_apps())
    if not apps or not composio_available():
        return None
    if _SESSION is not None and _SESSION_APPS == apps:
        return _SESSION
    try:
        client, user_id = _client()
        _SESSION = client.create(user_id=user_id, toolkits=list(apps))
        _SESSION_APPS = apps
        return _SESSION
    except Exception:  # noqa: BLE001
        _SESSION = None
        _SESSION_APPS = ()
        return None


def _spec_name(fn_name: str) -> str:
    return f"composio__{fn_name}"


def build_tool_specs() -> list[dict]:
    """OpenAI-style function specs for the Tool Router meta-tools of the user's
    enabled apps — a small bounded set (search/execute), never the full tool
    list, so we never exceed the provider's tools-array limit.

    Names are namespaced ``composio__<tool>`` so they can't collide with
    built-ins or MCP tools. Returns [] if composio isn't configured / enabled.
    """
    session = _session()
    if session is None:
        return []
    try:
        tools = session.tools()
    except Exception:  # noqa: BLE001
        return []
    specs: list[dict] = []
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else getattr(t, "function", t)
        name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")) or ""
        if not name:
            continue
        desc = fn.get("description", "") if isinstance(fn, dict) else getattr(fn, "description", "")
        params = fn.get("parameters", {}) if isinstance(fn, dict) else getattr(fn, "parameters", {})
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": _spec_name(name),
                    "description": desc or f"Composio tool {name}",
                    "parameters": params or {"type": "object", "properties": {}},
                },
            }
        )
    return specs


def is_composio_tool(name: str) -> bool:
    return name.startswith("composio__")


# Phrases that specifically mean "the app isn't connected", vs. a generic
# failure (rate limit, bad args). Adapted from Plandot's detect.py. Matched
# case-insensitively anywhere in the tool output.
_AUTH_PHRASES = (
    "not connected",
    "no active connection",
    "no connection",
    "authentication required",
    "authorization required",
    "please authorize",
    "please connect",
    "not authenticated",
    "unauthorized",
    "connect your",
    "requires authentication",
    "needs to be connected",
)


def looks_like_auth_error(text: str) -> bool:
    """True if ``text`` indicates a missing/expired Composio connection (as
    opposed to a generic failure)."""
    low = text.lower()
    return any(p in low for p in _AUTH_PHRASES)


def execute_tool(name: str, arguments: dict) -> str:
    """Run a Composio Tool Router meta-tool. ``name`` is ``composio__<tool>``;
    execution goes through the session so discovery/scoping is honored."""
    tool_slug = name[len("composio__") :]
    session = _session()
    if session is None:
        return "error: Composio session unavailable (not configured?)"
    try:
        res = session.execute(tool_slug, arguments=arguments or {})
    except Exception as exc:  # noqa: BLE001
        return f"error: {_friendly_error(exc)} (do not retry until fixed)"
    data = getattr(res, "data", None)
    if data is None and isinstance(res, dict):
        data = res.get("data", res)
    try:
        out = json.dumps(data if data is not None else res, default=str)[:8000]
    except Exception:  # noqa: BLE001
        out = str(res)[:8000]
    # If the failure is an auth/connection problem, tell the user how to fix it
    # (connect the app) instead of surfacing a raw "not connected" error.
    if looks_like_auth_error(out):
        out += (
            "\n\n[opendot] This looks like an unconnected app — run /composio "
            "to connect it, then retry."
        )
    return out
