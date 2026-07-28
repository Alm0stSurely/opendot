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

The composio SDK is an optional dependency (``pip install 'opendot[composio]'``);
everything here fails soft if it isn't installed.
"""

from __future__ import annotations

import json
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


def disable_app(slug: str) -> None:
    """Remove an app from the enabled list so its tools stop loading. Does not
    revoke the Composio-side connection (that stays until deleted on Composio)."""
    cfg = load_config()
    cfg["apps"] = [s for s in cfg.get("apps", []) if s != slug]
    save_config(cfg)


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


def begin_connect(slug: str) -> ConnectResult:
    """Start connecting a toolkit. If it needs OAuth, returns the redirect_url and
    the ConnectionRequest (caller opens the browser then calls
    ``wait_for_connection``). If it's a direct/no-auth connector, it's already
    active and ok=True."""
    try:
        client, user_id = _client()
        req = client.toolkits.authorize(user_id=user_id, toolkit=slug)
    except Exception as exc:  # noqa: BLE001
        return ConnectResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    redirect = getattr(req, "redirect_url", None)
    if redirect:
        return ConnectResult(ok=False, needs_auth=True, redirect_url=redirect, request=req)
    return ConnectResult(ok=True, request=req)


# ---- tool specs + execution (used by the Toolbox) ----

def build_tool_specs() -> list[dict]:
    """OpenAI-style function specs for all tools of the user's enabled apps.

    Names are namespaced ``composio__<slug>__<TOOL_SLUG>`` so they can't collide
    with built-ins or MCP tools and are recognisable as external/irreversible.
    Returns [] if composio isn't installed / configured / has no enabled apps.
    """
    apps = enabled_apps()
    if not apps or not composio_available():
        return []
    try:
        client, user_id = _client()
    except Exception:  # noqa: BLE001
        return []
    specs: list[dict] = []
    for slug in apps:
        try:
            tools = client.tools.get(user_id, toolkits=[slug], limit=1000)
        except Exception:  # noqa: BLE001
            continue
        for t in tools:
            fn = t.get("function", t) if isinstance(t, dict) else getattr(t, "function", t)
            name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")) or ""
            if not name:
                continue
            desc = fn.get("description", "") if isinstance(fn, dict) else getattr(fn, "description", "")
            params = fn.get("parameters", {}) if isinstance(fn, dict) else getattr(fn, "parameters", {})
            specs.append({
                "type": "function",
                "function": {
                    "name": f"composio__{slug}__{name}",
                    "description": desc or f"Composio {slug} tool {name}",
                    "parameters": params or {"type": "object", "properties": {}},
                },
            })
    return specs


def is_composio_tool(name: str) -> bool:
    return name.startswith("composio__")


def execute_tool(name: str, arguments: dict) -> str:
    """Run a namespaced composio tool. ``name`` is composio__<slug>__<TOOL_SLUG>."""
    try:
        _, _, tool_slug = name.split("__", 2)
    except ValueError:
        return f"error: malformed composio tool name {name!r}"
    try:
        client, user_id = _client()
        # skip the toolkit-version check — we execute the latest, same as Plandot
        # (otherwise the SDK raises ToolVersionRequiredError).
        res = client.tools.execute(
            tool_slug, arguments or {},
            user_id=user_id, dangerously_skip_version_check=True,
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: composio execution failed: {type(exc).__name__}: {exc}"
    # ToolExecutionResponse — prefer a JSON dump of its data/successful fields.
    data = getattr(res, "data", None)
    if data is None and isinstance(res, dict):
        data = res.get("data", res)
    try:
        return json.dumps(data if data is not None else res, default=str)[:8000]
    except Exception:  # noqa: BLE001
        return str(res)[:8000]
