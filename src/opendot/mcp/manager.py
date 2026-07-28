"""MCP client manager — connects opendot to external MCP servers.

MCP's client API is async and connection-oriented (a session must stay open for
its lifetime, via nested async context managers). opendot's tool layer is sync.
So this manager runs a dedicated **background asyncio loop on its own thread**,
opens every server session there and keeps them alive, and exposes plain sync
methods (`list_tools`, `call_tool`) that bridge onto that loop. All the async /
context-manager complexity is contained here.

Config lives at ``~/.opendot/mcp.json`` (Claude-Desktop-style):

    {
      "mcpServers": {
        "pyscrappy": { "command": "pyscrappy-mcp" },
        "github":    { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                       "env": {"GITHUB_TOKEN": "..."} },
        "remote":    { "url": "https://example.com/mcp" },         // http/sse
        "supabase":  { "url": "https://mcp.supabase.com/mcp?project_ref=abc",
                       "headers": {"Authorization": "Bearer <PAT>"} }  // authenticated remote
      }
    }

MCP tools are OPAQUE — opendot can't know if a call is reversible. So callers
treat every MCP tool as irreversible (confirm + mark ✗ in the ledger).
"""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opendot.reversibility.snapshots import store_root


def _config_path() -> Path:
    return store_root() / "mcp.json"


def load_mcp_config() -> dict[str, dict]:
    """Read ~/.opendot/mcp.json → {server_name: server_spec}. Empty if absent."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data.get("mcpServers", data) or {}


def save_mcp_config(servers: dict[str, dict]) -> None:
    """Write the full server map back to ~/.opendot/mcp.json."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")


def add_mcp_server(name: str, spec: dict) -> None:
    """Add/replace one server in the config."""
    servers = load_mcp_config()
    servers[name] = spec
    save_mcp_config(servers)


def remove_mcp_server(name: str) -> bool:
    """Remove a server; returns True if it existed."""
    servers = load_mcp_config()
    if name not in servers:
        return False
    del servers[name]
    save_mcp_config(servers)
    return True


@dataclass
class MCPTool:
    server: str
    name: str            # bare tool name on the server
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified(self) -> str:
        # namespaced so two servers can't collide; also signals it's an MCP tool
        return f"mcp__{self.server}__{self.name}"


class MCPManager:
    """Owns MCP server connections on a background loop; sync-facing API."""

    def __init__(self, config: dict[str, dict] | None = None) -> None:
        self.config = config if config is not None else load_mcp_config()
        self.tools: list[MCPTool] = []
        self.connected: list[str] = []
        self.errors: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, Any] = {}
        self._stack = None  # AsyncExitStack holding all open connections

    # -- lifecycle --
    def start(self) -> None:
        """Spin up the background loop and connect all configured servers."""
        if not self.config or self._thread is not None:
            return
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._connect_all(ready))
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="opendot-mcp")
        self._thread.start()
        ready.wait(timeout=30)  # let connections settle (best-effort)

    async def _connect_all(self, ready: threading.Event) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        self._stack = AsyncExitStack()
        for name, spec in self.config.items():
            try:
                read, write = await self._open_transport(spec)
                session = await self._stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self._sessions[name] = session
                self.connected.append(name)
                resp = await session.list_tools()
                for t in resp.tools:
                    self.tools.append(MCPTool(
                        server=name, name=t.name,
                        description=t.description or "",
                        input_schema=t.inputSchema or {"type": "object", "properties": {}},
                    ))
            except Exception as exc:  # noqa: BLE001 - a bad server must not kill the rest
                self.errors[name] = f"{type(exc).__name__}: {exc}"
        ready.set()

    async def _open_transport(self, spec: dict):
        """Return (read, write) streams for a stdio or http/sse server."""
        if spec.get("url"):
            url = spec["url"]
            headers = spec.get("headers") or None  # e.g. {"Authorization": "Bearer ..."}
            if url.rstrip("/").endswith("/sse"):
                from mcp.client.sse import sse_client
                return (await self._stack.enter_async_context(sse_client(url, headers=headers)))[:2]
            from mcp.client.streamable_http import streamablehttp_client
            return (await self._stack.enter_async_context(streamablehttp_client(url, headers=headers)))[:2]
        # stdio
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=spec["command"], args=spec.get("args", []),
            env=spec.get("env") or None,
        )
        return await self._stack.enter_async_context(stdio_client(params))

    # -- sync bridge --
    def _run(self, coro, timeout: float = 120.0):
        if self._loop is None:
            raise RuntimeError("MCP manager not started")
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def call_tool(self, server: str, name: str, args: dict[str, Any]) -> str:
        """Call an MCP tool synchronously; return a text result."""
        session = self._sessions.get(server)
        if session is None:
            return f"error: MCP server {server!r} not connected"

        async def _call():
            res = await session.call_tool(name, args or {})
            # prefer structured content, else concatenate text blocks
            structured = getattr(res, "structuredContent", None)
            if structured:
                return json.dumps(structured, default=str)
            parts = []
            for block in getattr(res, "content", []) or []:
                parts.append(getattr(block, "text", "") or str(block))
            return "\n".join(parts) or "(no content)"

        try:
            return self._run(_call())
        except Exception as exc:  # noqa: BLE001
            return f"error calling {server}.{name}: {exc}"

    def shutdown(self) -> None:
        if self._loop and self._loop.is_running():
            async def _close():
                if self._stack:
                    await self._stack.aclose()
            try:
                self._run(_close(), timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
