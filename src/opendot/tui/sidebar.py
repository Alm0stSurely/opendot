"""The right-rail sidebar: task title, context/cost meter, connected providers,
Composio status, and the live reversibility ledger — the section that is
opendot's reason to exist."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from opendot.agent.loop import Agent
from opendot.tui.helpers import _context_window


class Sidebar(Static):
    """Right rail (opencode-style): task title, Context meter, and the live
    reversibility ledger — the section no other agent's sidebar has."""

    def __init__(self, agent: Agent) -> None:
        super().__init__(id="sidebar")
        self.agent = agent
        self.task_title = ""  # set from the user's latest message

    def _section(self, t: Text, name: str) -> None:
        t.append(f"{name}\n", style="bold")

    def render(self):
        a = self.agent
        u = a.usage
        t = Text()

        # -- task title (like opencode's top line) --
        title = self.task_title or "opendot session"
        t.append(title + "\n\n", style="bold")

        # -- Context --
        self._section(t, "Context")
        t.append(f"{u.total_tokens:,} tokens\n", style="dim")
        window = _context_window(a.config.model)
        if window:
            pct = min(100, round(100 * u.total_tokens / window))
            t.append(f"{pct}% used\n", style="dim")
        t.append(f"${u.cost_usd:.4f} spent\n\n", style="dim")

        # -- Model --
        self._section(t, "Model")
        t.append(f"{a.config.model}\n\n", style="cyan")

        # -- MCP servers (only if any are configured/connected) --
        mgr = getattr(a, "mcp", None)
        if mgr is not None and (mgr.connected or mgr.errors):
            self._section(t, "MCP")
            for name in mgr.connected:
                n_tools = sum(1 for mt in mgr.tools if mt.server == name)
                t.append("• ", style="dim")
                t.append(f"{name} ", style="green")
                t.append(f"({n_tools} tools)\n", style="dim")
            for name, err in mgr.errors.items():
                t.append("• ", style="dim")
                t.append(f"{name} failed\n", style="red")
            t.append("\n")

        # -- Providers (which API keys are set this session) --
        try:
            import os
            from opendot.providers import known_key_vars
            connected_providers = [var for var in known_key_vars() if os.environ.get(var)]
        except Exception:  # noqa: BLE001
            connected_providers = []
        if connected_providers:
            self._section(t, "Providers")
            for var in connected_providers:
                base = var.replace("_API_KEY", "").replace("_TOKEN", "")
                label = base.replace("_", " ").title()  # no stray underscores
                t.append("• ", style="dim")
                t.append(f"{label} ", style="green")
                t.append("✓\n", style="green")
            t.append("\n")

        # -- Composio (show once a key is set; then list enabled apps) --
        try:
            from opendot.tools import composio as composio_tools
            cx_configured = composio_tools.is_configured()
            capps = composio_tools.enabled_apps()
        except Exception:  # noqa: BLE001
            cx_configured, capps = False, []
        if cx_configured:
            self._section(t, "Composio")
            t.append("connected ", style="green")
            t.append("✓\n", style="green")
            for slug in capps:
                t.append("• ", style="dim")
                t.append(f"{slug}\n", style="green")
            if not capps:
                t.append("no apps enabled yet\n", style="dim")
            t.append("\n")

        # -- Ledger (the differentiator) --
        self._section(t, "Ledger")
        t.append("undoable ↺ · irreversible ✗\n", style="dim")
        history = a.reversibility.history()
        if not history:
            t.append("no actions yet\n", style="dim")
        else:
            for e in history[-16:]:
                mark, style = ("↺", "green") if e.reversible else ("✗", "red")
                detail = e.detail.rsplit("/", 1)[-1][:20]
                t.append("• ", style="dim")
                t.append(f"{mark} ", style=style)
                t.append(f"{e.kind[:5]} {detail}\n", style="dim")
        t.append("\nctrl+z undo · ctrl+l log", style="dim italic")
        return t
