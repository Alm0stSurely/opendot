"""opendot's full-screen TUI (Textual).

Layout:
  ┌───────────────────────────────┬──────────────────┐
  │ transcript (streamed thinking, │  sidebar:        │
  │ tool activity, answers)        │   model          │
  │                                │   context/cost   │
  │                                │   ACTION LEDGER   │  <- the differentiator
  ├───────────────────────────────┴──────────────────┤
  │ > input                                            │
  └────────────────────────────────────────────────────┘

The sidebar leads with the reversibility ledger — every action, marked undoable
or irreversible — which is opendot's reason to exist and something the other
terminal agents' UIs don't have. Ctrl+Z / the /undo command walk it back live.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Static

from opendot.agent.loop import Agent


class ConfirmModal(ModalScreen[bool]):
    """A blocking yes/no modal for irreversible commands. Returns True to run."""

    CSS = """
    ConfirmModal { align: center middle; }
    #box { width: 70%; max-width: 90; height: auto; padding: 1 2;
           border: thick $warning; background: $surface; }
    #q { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: center; }
    Button { margin: 0 1; }
    """

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(Text.assemble(
                ("⚠ irreversible action\n\n", "bold yellow"),
                (self._prompt, ""),
            ), id="q")
            with Horizontal(id="buttons"):
                yield Button("Run it", variant="error", id="yes")
                yield Button("Skip", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)

# Per-tool glyphs so each step reads at a glance (opencode/Plandot-style).
_TOOL_ICONS = {
    "read_file": "📖", "write_file": "✎", "edit": "✂", "list_files": "▤",
    "grep": "🔍", "glob": "❊", "run_shell": "❯",
}


def _render_tool_result(tool: str, result: str):
    """Render a tool's result. File edits (write_file/edit) show a colored diff;
    other tools show a short preview. This is the 'see exactly what changed'
    transparency that reinforces reversibility."""
    result = result.rstrip()
    if tool in {"write_file", "edit"} and "@@" in result:
        t = Text()
        for line in result.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                t.append(line + "\n", style="green")
            elif line.startswith("-") and not line.startswith("---"):
                t.append(line + "\n", style="red")
            elif line.startswith("@@"):
                t.append(line + "\n", style="cyan")
            elif line.startswith(("+++", "---")):
                t.append(line + "\n", style="dim")
            else:
                t.append(line + "\n", style="dim")
        return t
    # non-diff: first couple of lines, dimmed
    lines = result.splitlines() or ["(done)"]
    preview = "\n".join(lines[:3])
    if len(lines) > 3:
        preview += f"\n  … (+{len(lines) - 3} lines)"
    return Text(preview, style="dim")


def _context_window(model: str) -> int | None:
    """Real context-window size from LiteLLM's model database (no guessing).

    Returns None if LiteLLM doesn't know the model (e.g. some local models),
    in which case the sidebar just omits the "% used" line.
    """
    try:
        import litellm

        info = litellm.get_model_info(model)
        return info.get("max_input_tokens") or info.get("max_tokens")
    except Exception:  # noqa: BLE001 - unknown model / lookup failure
        return None


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


class OpendotTUI(App):
    ENABLE_COMMAND_PALETTE = True  # ctrl+p

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }                     /* main column + sidebar fill the middle */
    #main { width: 3fr; height: 1fr; }         /* left column: transcript + input + mode */
    #transcript { height: 1fr; padding: 0 1; } /* fills the space above the input */
    #sidebar { width: 34; height: 1fr; padding: 1; border-left: solid $panel; }

    /* Input: taller box, bounded by the left column so it never crosses the
       sidebar (opencode-style). Mode line sits just under it. */
    #input { height: 5; border: round $accent; margin: 0 1; padding: 0 1; }
    #modeline { height: 1; margin: 0 1; color: $text-muted; }

    /* Each message type is a visually distinct block. */
    .msg { margin: 1 0 0 0; }
    .user   { background: $boost; color: $text; text-style: bold;
              border-left: thick $accent; padding: 0 1; }
    .think  { color: $text-muted; text-style: italic; margin: 0 0 0 2; }
    .answer { color: $text; border-left: thick $success; padding: 0 1; }
    .tool   { color: $accent; text-style: bold; margin: 1 0 0 0; }
    .toolout{ color: $text-muted; margin: 0 0 0 2; }
    .err    { color: $error; text-style: bold; border-left: thick $error; padding: 0 1; }
    .sys    { color: $text-muted; text-style: italic; }
    """

    BINDINGS = [
        Binding("ctrl+z", "undo", "Undo last action"),
        Binding("ctrl+l", "log", "Show ledger note"),
        Binding("escape", "interrupt", "Interrupt", show=False),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent
        self._turn_worker = None
        self._busy = False
        # Give the agent a confirm callback that shows a blocking modal. It's
        # invoked from a worker thread (tool runs via asyncio.to_thread), so
        # call_from_thread is the correct, non-deadlocking bridge to the UI.
        agent.toolbox._confirm = self._confirm_from_thread

    def _confirm_from_thread(self, prompt: str) -> bool:
        try:
            return bool(self.call_from_thread(self.push_screen_wait, ConfirmModal(prompt)))
        except Exception:  # noqa: BLE001 - if anything goes wrong, fail safe (decline)
            return False

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        yield Header(show_clock=False)
        with Horizontal(id="body"):
            # Left column: transcript fills, input + mode line pinned at its bottom.
            with Vertical(id="main"):
                yield VerticalScroll(id="transcript")
                yield Input(placeholder="Ask opendot…  (/help /log /undo /clear /compact)", id="input")
                yield Static(self._mode_line(), id="modeline")
            yield Sidebar(self.agent)
        yield Footer()

    def _mode_line(self):
        return Text.assemble(
            ("  esc ", "bold cyan"), ("interrupt", "dim"),
            ("  ·  ", "dim"),
            ("ctrl+p ", "bold cyan"), ("commands", "dim"),
        )

    def on_mount(self) -> None:
        self.title = "opendot"
        self.sub_title = self.agent.config.workdir
        self._write("opendot ready. Type a message, or /help.", "sys")
        self.query_one("#input", Input).focus()

    # -- transcript helpers --
    def _write(self, renderable, cls: str = "") -> None:
        w = Static(renderable, classes=f"msg {cls}".strip())
        self.query_one("#transcript", VerticalScroll).mount(w)
        self.call_after_refresh(self._scroll_end)

    def _scroll_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _refresh_sidebar(self) -> None:
        self.query_one(Sidebar).refresh()

    # -- input handling --
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#input", Input).value = ""
        if not text or self._busy:
            return

        if text.lower() in {"exit", "quit", "/exit", "/quit"}:
            self.exit()
            return
        if text.startswith("/"):
            self._slash(text)
            return

        # Header: system username + local time, then the message.
        import datetime
        import getpass
        try:
            who = getpass.getuser()
        except Exception:  # noqa: BLE001
            who = "you"
        now = datetime.datetime.now().strftime("%H:%M")
        header = Text.assemble((who, "bold"), (f"  {now}", "dim"))
        self._write(Text.assemble(header, "\n", (text, "")), "user")
        # Use the message as the sidebar's task title (first ~40 chars).
        sb = self.query_one(Sidebar)
        sb.task_title = text[:40] + ("…" if len(text) > 40 else "")
        sb.refresh()
        self._busy = True
        self._turn_worker = self.run_worker(self._run_turn(text), exclusive=True)

    def _slash(self, text: str) -> None:
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.lower()
        a = self.agent
        if cmd == "help":
            self._write("commands: /log /undo [id] /clear /compact /model /help", "sys")
        elif cmd == "clear":
            a.reset()
            self._write("context cleared", "sys")
        elif cmd == "compact":
            n = a.compact()
            self._write(f"compacted: dropped {n} old message(s)", "sys")
        elif cmd == "model":
            self._write(f"model: {a.config.model}", "sys")
        elif cmd == "log":
            self.action_log()
        elif cmd == "undo":
            self._do_undo(rest.strip() or None)
        else:
            self._write(f"unknown command: /{cmd}", "sys")

    async def _run_turn(self, message: str) -> None:
        mode = None
        buf: list[str] = []

        def flush_answer():
            if buf:
                from rich.console import Group
                self._write(
                    Group(Text("opendot", style="bold green"), Markdown("".join(buf))),
                    "answer",
                )
                buf.clear()

        try:
            async for ev in self.agent.run(message):
                if ev.type == "thinking":
                    if mode != "think":
                        flush_answer()
                        mode = "think"
                    self._write(Text(ev.text.rstrip(), style="italic"), "think")
                elif ev.type == "text":
                    mode = "answer"
                    buf.append(ev.text)
                elif ev.type == "tool_start":
                    flush_answer(); mode = None
                    icon = _TOOL_ICONS.get(ev.tool, "▸")
                    args = ", ".join(f"{k}={v!r}"[:50] for k, v in ev.args.items())
                    self._write(Text(f"{icon} {ev.tool}  ", style="bold").append(f"({args})", style="dim"), "tool")
                elif ev.type == "tool_end":
                    self._write(_render_tool_result(ev.tool, ev.result), "toolout")
                    self._refresh_sidebar()
                elif ev.type == "explorer_start":
                    flush_answer(); mode = None
                    self._write(Text(f"⇉ explorer {ev.lane + 1}: {ev.text}", style="bold magenta"), "tool")
                elif ev.type == "explorer_step":
                    self._write(Text(f"    [{ev.lane + 1}] {ev.text}", style="magenta"), "toolout")
                elif ev.type == "explorer_done":
                    first = (ev.text.strip().splitlines() or ["(done)"])[0]
                    self._write(Text(f"    [{ev.lane + 1}] ✓ {first[:100]}", style="dim magenta"), "toolout")
                elif ev.type == "error":
                    flush_answer(); mode = None
                    self._write(Text(ev.text), "err")
            flush_answer()
        finally:
            self._busy = False
            self._refresh_sidebar()

    # -- actions --
    def action_interrupt(self) -> None:
        """Esc: cancel the in-flight turn."""
        if self._busy and self._turn_worker is not None:
            self._turn_worker.cancel()
            self._busy = False
            self._write("interrupted", "sys")

    def action_undo(self) -> None:
        if not self._busy:
            self._do_undo(None)

    def _do_undo(self, snap_id: str | None) -> None:
        rev = self.agent.reversibility
        entries = rev.history()
        if not entries:
            self._write("nothing to undo", "sys")
            return
        if snap_id:
            target = next((e for e in entries if e.id == snap_id or e.id[-3:] == snap_id), None)
            if not target:
                self._write(f"no action {snap_id} (see /log)", "sys")
                return
            rev.restore_to(target.snapshot_before)
            self._write(f"restored workspace to before action {target.id}", "sys")
        else:
            undone = rev.undo_last()
            self._write(f"undid last action ({undone.kind}: {undone.detail.rsplit('/',1)[-1]})", "sys")
        self._refresh_sidebar()

    def action_log(self) -> None:
        history = self.agent.reversibility.history()
        if not history:
            self._write("no actions recorded", "sys")
            return
        t = Text()
        t.append("action history\n", style="bold")
        for e in history:
            mark = "↺" if e.reversible else "✗ irreversible"
            t.append(f"  {e.id}  {mark}  {e.kind}  {e.detail}\n", style="dim")
        self._write(t, "")


def run_tui(agent: Agent) -> None:
    OpendotTUI(agent).run()
