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


# Pre-warm textual-image's terminal capability probe at import time — i.e. BEFORE
# the Textual app puts the terminal in raw mode and focuses the input. The probe
# sends a Device Attributes query ("\e[c") and reads the reply; if it runs later
# (lazily during compose), that reply ("^[?1;2c") leaks into the focused input.
# Guarded to a real TTY so it never hangs in tests / piped / one-shot mode.
import sys as _sys
try:
    if _sys.stdin.isatty() and _sys.stdout.isatty():
        from textual_image.widget import Image as _ProbeImage  # noqa: F401 - import triggers the probe
except Exception:  # noqa: BLE001 - probe/import failure must never block startup
    pass


# Path to the logo image shown on the welcome screen (rendered via textual-image).
from pathlib import Path as _Path
_LOGO_PATH = _Path(__file__).resolve().parent.parent.parent / "assets" / "logo-full.png"


def _row_bar(left: str, right: str, right_style: str = "dim", left_style: str = ""):
    """A full-width row: left text, right text flush to the right edge.

    Uses a grid so the right column auto-aligns to the widget's actual width at
    render time (no manual padding / size measurement)."""
    from rich.table import Table

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(Text(left, style=left_style), Text(right, style=right_style))
    return grid


def _title_bar(title: str, hint: str = "esc cancel"):
    """A modal title row: bold title on the left, dim hint flush to the right."""
    return _row_bar(title, hint, right_style="dim", left_style="bold")


class ConfirmModal(ModalScreen[bool]):
    """A blocking yes/no modal for irreversible commands. Returns True to run."""

    CSS = """
    ConfirmModal { align: center middle; }
    #box { width: 70%; max-width: 90; height: auto; padding: 1 2;
           border: round $warning; background: $surface; }
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


class SearchListModal(ModalScreen[str | None]):
    """A searchable, keyboard-navigable list picker (opencode-style).

    ``items`` is a list of (value, label, group) tuples. Typing filters by
    label; ↑/↓ move; Enter selects (returns the value); Esc cancels (None).
    """

    CSS = """
    SearchListModal { align: center middle; }
    #box { width: 70%; max-width: 90; height: 80%; padding: 1 2;
           border: round $accent; background: $surface; }
    #title { text-style: bold; margin-bottom: 1; }
    #search { margin-bottom: 1; }
    #list { height: 1fr; }
    """

    def __init__(self, title: str, items: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._title = title
        self._items = items  # (value, label, group)

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList

        with Vertical(id="box"):
            yield Static(id="title")
            yield Input(placeholder="Search…", id="search")
            yield OptionList(id="list")

    def on_mount(self) -> None:
        self._set_title()
        self._populate("")
        self.query_one("#search", Input).focus()

    def _set_title(self) -> None:
        self.query_one("#title", Static).update(_title_bar(self._title, "esc cancel"))

    def _populate(self, query: str) -> None:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        q = query.lower()
        ol = self.query_one("#list", OptionList)
        ol.clear_options()
        last_group = None
        self._values: list[str] = []
        for item in self._items:
            value, label, group = item[0], item[1], item[2]
            status = item[3] if len(item) > 3 else ""  # optional right-aligned status
            if q and q not in label.lower():
                continue
            if group and group != last_group:
                ol.add_option(Option(Text(group.upper(), style="bold magenta"), disabled=True))
                last_group = group
            # Status (e.g. "✓ enabled") is rendered flush-right via a grid.
            prompt = _row_bar(label, status, "green") if status else Text(label)
            ol.add_option(Option(prompt, id=str(len(self._values))))
            self._values.append(value)
        if self._values:
            ol.highlighted = 1 if (self._items and self._items[0][2]) else 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the search box selects the current highlight.
        from textual.widgets import OptionList

        event.stop()  # don't let Enter bubble to the main chat input
        ol = self.query_one("#list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id is not None:
                self.dismiss(self._values[int(opt.id)])

    def on_option_list_option_selected(self, event) -> None:
        if event.option.id is not None:
            self.dismiss(self._values[int(event.option.id)])

    def on_key(self, event) -> None:
        from textual.widgets import OptionList

        if event.key == "escape":
            self.dismiss(None)
        elif event.key in ("down", "up"):
            # Let the arrow keys drive the list while focus stays in the search box.
            ol = self.query_one("#list", OptionList)
            if event.key == "down":
                ol.action_cursor_down()
            else:
                ol.action_cursor_up()
            event.stop()


class ApiKeyModal(ModalScreen[str | None]):
    """A single password field to paste an API key. Returns the key, or None."""

    CSS = """
    ApiKeyModal { align: center middle; }
    #box { width: 60%; max-width: 80; height: auto; padding: 1 2;
           border: round $accent; background: $surface; }
    #title { text-style: bold; }
    #subtitle { color: $text-muted; text-style: italic; margin-bottom: 1; }
    """

    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__()
        self._provider = provider
        self._env_var = env_var

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(_title_bar(f"Connect {self._provider}"), id="title")
            yield Static(f"sets {self._env_var} for this session", id="subtitle")
            yield Input(placeholder="Paste API key…", password=True, id="key")

    def on_mount(self) -> None:
        self.query_one("#key", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # don't let Enter bubble to the main chat input
        self.dismiss(event.value.strip() or None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class McpAddModal(ModalScreen[dict | None]):
    """Form to add an MCP server. Returns {"name", "spec"} or None.

    One field decides the transport: a value starting with http(s):// is a
    remote server (with an optional Authorization header); anything else is a
    stdio launch command (split on spaces).
    """

    CSS = """
    McpAddModal { align: center middle; }
    #box { width: 70%; max-width: 90; height: auto; padding: 1 2;
           border: round $accent; background: $surface; }
    #title { text-style: bold; margin-bottom: 1; }
    Input { margin-bottom: 1; }
    #hint { color: $text-muted; text-style: italic; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(_title_bar("Add an MCP server"), id="title")
            yield Input(placeholder="name (e.g. github, supabase)", id="name")
            yield Input(placeholder="https://…/mcp   OR   npx -y @scope/server args…", id="target")
            yield Input(placeholder="Authorization header (remote only, optional)", id="header")
            yield Static(
                "enter submit · a value starting with http(s):// is treated as a remote URL",
                id="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        target = self.query_one("#target", Input).value.strip()
        header = self.query_one("#header", Input).value.strip()
        if not name or not target:
            return  # name + target required; keep the form open
        if target.lower().startswith(("http://", "https://")):
            spec: dict = {"url": target}
            if header:
                k, _, v = header.partition("=") if "=" in header else header.partition(":")
                spec["headers"] = {k.strip(): v.strip()}
        else:
            parts = target.split()
            spec = {"command": parts[0]}
            if len(parts) > 1:
                spec["args"] = parts[1:]
        self.dismiss({"name": name, "spec": spec})

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # don't let Enter bubble to the main chat input
        self._submit()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


# Slash commands shown in the autocomplete popup (name, one-line description).
# Single source of truth — the popup filters this list.
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/model", "switch model (searchable picker)"),
    ("/provider", "connect a provider + API key"),
    ("/mcp", "manage MCP servers"),
    ("/composio", "connect apps (Gmail, Slack, …)"),
    ("/log", "show the action ledger"),
    ("/undo", "revert the last action ( /undo <id> )"),
    ("/clear", "reset the conversation"),
    ("/compact", "trim old turns to free context"),
    ("/help", "list commands"),
]


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

        # -- Providers (which API keys are set this session) --
        try:
            import os
            from opendot.providers import CONNECTABLE_PROVIDERS
            connected_providers = [n for n, var in CONNECTABLE_PROVIDERS if os.environ.get(var)]
        except Exception:  # noqa: BLE001
            connected_providers = []
        if connected_providers:
            self._section(t, "Providers")
            for name in connected_providers:
                t.append("• ", style="dim")
                t.append(f"{name} ", style="green")
                t.append("✓\n", style="green")
            t.append("\n")

        # -- Composio (show once a key is set; then list enabled apps) --
        try:
            from opendot import composio_tools
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


class OpendotTUI(App):
    ENABLE_COMMAND_PALETTE = True  # ctrl+p

    CSS = """
    Screen { layout: vertical; layers: base overlay; }
    #body { height: 1fr; }                     /* main column + sidebar fill the middle */
    #main { width: 3fr; height: 1fr; }         /* left column: transcript + input + mode */
    #transcript { height: 1fr; padding: 0 1; } /* fills the space above the input */
    #sidebar { width: 34; height: 1fr; padding: 1; border-left: solid $panel; }

    /* Welcome layout: small centered logo with the input right below it,
       the whole group centered in the screen (opencode-style). #main's
       `align: center middle` centers the children — no auto margins needed. */
    /* The logo wrapper is a full-width Center; hidden in the normal layout. */
    #welcome-wrap { display: none; }
    #welcome { width: auto; height: 6; }
    Screen.-welcome #transcript { display: none; }
    Screen.-welcome #sidebar { display: none; }
    /* Anchor the welcome group near the top-center with fixed padding rather
       than dynamic middle-centering — so opening the command popup grows the
       column downward instead of re-centering (and shoving) the logo. */
    Screen.-welcome #main { width: 1fr; height: 1fr; align: center top; padding-top: 9; }
    /* All three welcome children share width:60% so #main's align centers them
       as one column. The logo image inside is centered by its Center wrapper. */
    Screen.-welcome #welcome-wrap {
        display: block; width: 60%; max-width: 90; height: auto; margin-bottom: 1;
    }
    Screen.-welcome #input { width: 70%; max-width: 100; margin: 0; }
    Screen.-welcome #modeline { width: 70%; max-width: 100; margin: 0; }

    /* Input: taller box, bounded by the left column so it never crosses the
       sidebar (opencode-style). Mode line sits just under it. */
    #input { height: 5; border: round $accent; margin: 0 1; padding: 0 1; }
    #modeline { height: 1; margin: 0 1; color: $text-muted; }

    /* Slash-command autocomplete popup — a FLOATING overlay on its own layer,
       so it renders on top of the logo/transcript without pushing them down.
       It's absolutely positioned each time it opens, just above the input
       (see _position_popup), matching the input's x and width. */
    #cmdpopup { layer: overlay; height: auto; max-height: 10; padding: 0;
                border: round $panel; background: $surface; }
    #cmdpopup > .option-list--option-highlighted { background: $accent; color: $text; }

    /* Message blocks — understated, opencode-style. The answer is the only
       block with real visual weight; everything else recedes. */
    .msg { margin: 1 0 0 0; }
    .user   { color: $text; border-left: solid $accent; padding: 0 1; }
    .think  { color: $text-muted; margin: 0 0 0 2; }
    .answer { color: $text; border-left: solid #2dd4bf; padding: 0 1; }
    .tool   { color: $text-muted; margin: 1 0 0 2; }
    .toolout{ color: $text-muted; margin: 0 0 0 4; }
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
        from textual.containers import Center, Vertical
        from textual.widgets import OptionList

        yield Header(show_clock=False)
        with Horizontal(id="body"):
            # Left column: transcript fills, input + mode line pinned at its bottom.
            with Vertical(id="main"):
                # Welcome logo — the real PNG, shown until the first message.
                # textual-image auto-picks TGP/Sixel on capable terminals and
                # falls back to Unicode blocks elsewhere. If it can't load at
                # all, we degrade to a text wordmark. Wrapped in Center so the
                # auto-width image is reliably horizontally centred.
                with Center(id="welcome-wrap"):
                    yield self._welcome_widget()
                yield VerticalScroll(id="transcript")
                yield Input(placeholder='Ask opendot…   "fix broken tests"', id="input")
                yield Static(self._mode_line(), id="modeline")
            yield Sidebar(self.agent)
        # Slash-command autocomplete — a floating overlay (own layer) so it
        # renders ON TOP of the transcript/logo without pushing anything down.
        popup = OptionList(id="cmdpopup")
        popup.display = False
        popup.can_focus = False  # keep typing focus in the input
        yield popup
        yield Footer()

    def _welcome_widget(self):
        """The welcome logo. Real PNG via textual-image where supported, with a
        text wordmark fallback if the package/image can't load.

        The logo PNG is transparent; terminal image protocols flatten alpha onto
        a solid colour. So we pre-composite it onto the TUI's own background
        colour (#121212) — then the image's edges blend into the screen instead
        of showing a black box."""
        try:
            from textual_image.widget import Image
            if _LOGO_PATH.exists():
                return Image(self._logo_on_theme_bg(), id="welcome")
        except Exception:  # noqa: BLE001 - any failure → text fallback
            pass
        return Static(Text("opendot", style="bold"), id="welcome")

    def _logo_on_theme_bg(self):
        """Load the transparent logo, trim its transparent margins (so the
        wordmark centres correctly), and flatten it onto the theme background so
        there's no visible box. Returns a PIL image (or the path on failure).

        ANSI themes report their background as an ANSI sentinel (no real RGB —
        the terminal owns it), so we can't pick a matching colour; in that case
        we leave the PNG transparent and let the image protocol composite it."""
        try:
            from PIL import Image as PILImage
            logo = PILImage.open(_LOGO_PATH).convert("RGBA")
            bbox = logo.getbbox()  # tight box around non-transparent pixels
            if bbox:
                logo = logo.crop(bbox)

            bg = self.screen.styles.background
            # ANSI-defaulted background: real colour unknown → don't composite.
            if bg is None or getattr(bg, "ansi", None) is not None:
                return logo
            canvas = PILImage.new("RGBA", logo.size, (bg.r, bg.g, bg.b, 255))
            canvas.alpha_composite(logo)
            return canvas.convert("RGB")
        except Exception:  # noqa: BLE001
            return str(_LOGO_PATH)

    def watch_theme(self, theme_name: str) -> None:
        """Re-composite the welcome logo when the theme changes, so its
        background keeps matching the (possibly light) screen. Only matters
        while the welcome screen is still up — it's gone after the first message.
        Recomposites in place (Image.image is settable) to avoid a widget swap."""
        def _rebuild():
            try:
                if not self.screen.has_class("-welcome"):
                    return
                w = self.query_one("#welcome")
                if hasattr(w, "image"):  # textual-image widget; text fallback has none
                    w.image = self._logo_on_theme_bg()
            except Exception:  # noqa: BLE001 - never let a theme change crash the UI
                pass

        # Defer: when watch_theme fires, screen.styles.background still holds the
        # OLD theme colour. Recompute after the refresh applies the new theme.
        self.call_after_refresh(_rebuild)

    def _mode_line(self):
        return Text.assemble(
            ("  esc ", "bold cyan"), ("interrupt", "dim"),
            ("  ·  ", "dim"),
            ("ctrl+p ", "bold cyan"), ("commands", "dim"),
        )

    def _dismiss_welcome(self) -> None:
        """Hide the welcome logo and reveal the normal transcript+sidebar layout.
        Called once, on the first user message."""
        if self.screen.has_class("-welcome"):
            self.screen.remove_class("-welcome")

    def on_mount(self) -> None:
        self.title = "opendot"
        self.sub_title = self.agent.config.workdir
        # Drop the ANSI themes — their background is the terminal's (unknown to
        # us), so the welcome logo can't be composited to match them.
        for _t in ("ansi-dark", "ansi-light"):
            try:
                self.unregister_theme(_t)
            except Exception:  # noqa: BLE001
                pass
        # Start on the welcome screen (logo only); first message reveals the rest.
        self.screen.add_class("-welcome")
        self.query_one("#input", Input).focus()

    # -- transcript helpers --
    def _write(self, renderable, cls: str = "") -> None:
        w = Static(renderable, classes=f"msg {cls}".strip())
        self.query_one("#transcript", VerticalScroll).mount(w)
        self.call_after_refresh(self._scroll_end)

    def _clear_transcript(self) -> None:
        """Wipe the on-screen transcript (like clearing a terminal). Does not
        touch the conversation/context — that's what /clear adds."""
        for w in self.query("#transcript > .msg"):
            w.remove()

    def _scroll_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _refresh_sidebar(self) -> None:
        self.query_one(Sidebar).refresh()

    # -- slash-command autocomplete --
    def _popup(self):
        from textual.widgets import OptionList
        return self.query_one("#cmdpopup", OptionList)

    @property
    def _popup_open(self) -> bool:
        return self._popup().display

    def _matches(self, text: str) -> list[tuple[str, str]]:
        """Commands matching the current input. Active only while the line is a
        single '/word' with no space yet (i.e. still choosing a command)."""
        if not text.startswith("/") or " " in text:
            return []
        q = text[1:].lower()
        return [(n, d) for n, d in SLASH_COMMANDS if n[1:].lower().startswith(q)]

    def _sync_popup(self, text: str) -> None:
        from textual.widgets.option_list import Option

        matches = self._matches(text)
        popup = self._popup()
        if not matches:
            popup.display = False
            return
        popup.display = True
        popup.clear_options()
        # Use the expanding-grid row so the description right-aligns to the popup's
        # ACTUAL width at render time — no manual padding that can overshoot the
        # width and wrap the description onto a second line.
        for name, desc in matches:
            popup.add_option(Option(_row_bar(name, desc, right_style="dim", left_style="bold"), id=name))
        popup.highlighted = 0
        # Float it just above the input (after layout settles so heights are known).
        self.call_after_refresh(self._position_popup)

    def _position_popup(self) -> None:
        """Place the floating popup directly above the input, matching its x and
        width. Runs after refresh so the input region and popup height are known."""
        try:
            popup = self._popup()
            if not popup.display:
                return
            inp = self.query_one("#input", Input)
            ir = inp.region
            # Match the input's outer box. The popup's round border adds 2 cells
            # of width, so set content width to the input width minus the border.
            popup.styles.width = max(10, ir.width - 2)
            popup.styles.height = "auto"
            # Sit its bottom flush with the top of the input.
            top = max(0, ir.y - popup.outer_size.height)
            popup.styles.offset = (ir.x, top)
        except Exception:  # noqa: BLE001
            pass

    def _highlighted_command(self) -> str | None:
        popup = self._popup()
        if popup.highlighted is None:
            return None
        return self._popup().get_option_at_index(popup.highlighted).id

    def _accept_popup(self, *, run: bool) -> None:
        """Pick the highlighted command. If ``run``, execute it immediately
        (Enter); otherwise just complete it into the input so the user can add
        an argument (Tab), e.g. `/undo 4` or `/model gpt-5.1`."""
        name = self._highlighted_command()
        if name is None:
            return
        inp = self.query_one("#input", Input)
        self._popup().display = False
        if run:
            inp.value = ""
            self._slash(name)
        else:
            inp.value = name + " "
            inp.cursor_position = len(inp.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input":
            self._sync_popup(event.value)

    def on_key(self, event) -> None:
        """Drive the autocomplete popup from the keyboard while it's open."""
        if not self._popup_open:
            return
        popup = self._popup()
        if event.key == "down":
            popup.action_cursor_down(); event.stop(); event.prevent_default()
        elif event.key == "up":
            popup.action_cursor_up(); event.stop(); event.prevent_default()
        elif event.key == "tab":
            self._accept_popup(run=False); event.stop(); event.prevent_default()
        elif event.key == "escape":
            popup.display = False; event.stop(); event.prevent_default()

    # -- input handling --
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only the main prompt submits chat. A submit from any other Input
        # (e.g. a modal's password/search field) must never become a message —
        # modals also .stop() it, this is defence in depth so secrets can't leak.
        if event.input.id != "input":
            return
        # If the popup is open, Enter runs the highlighted command immediately.
        if self._popup_open:
            self._accept_popup(run=True)
            return
        text = event.value.strip()
        self.query_one("#input", Input).value = ""
        if not text or self._busy:
            return

        if text.lower() in {"exit", "quit", "/exit", "/quit"}:
            self.exit()
            return
        # Bare `clear`/`cls` = clear the screen (transcript), like a terminal —
        # not a shell command and not a conversation reset. `/clear` does both.
        if text.lower() in {"clear", "cls"}:
            self._clear_transcript()
            return
        if text.startswith("/"):
            self._slash(text)
            return

        # First real message leaves the welcome screen for the full layout.
        self._dismiss_welcome()

        # Header: system username + local time, then the message.
        import datetime
        import getpass
        try:
            who = getpass.getuser()
        except Exception:  # noqa: BLE001
            who = "you"
        now = datetime.datetime.now().strftime("%H:%M")
        header = Text.assemble((who, "dim"), (f"  {now}", "dim"))
        self._write(Text.assemble(header, "\n", (text, "")), "user")
        # Use the message as the sidebar's task title (first ~40 chars).
        sb = self.query_one(Sidebar)
        sb.task_title = text[:40] + ("…" if len(text) > 40 else "")
        sb.refresh()

        # If the model's API key isn't set, guide the user instead of letting the
        # turn fail with a raw provider auth error.
        if self._missing_key_hint():
            return

        self._busy = True
        self._turn_worker = self.run_worker(self._run_turn(text), exclusive=True)

    def _missing_key_hint(self) -> bool:
        """If the current model needs an API key that isn't set, write a friendly
        hint to the transcript and return True (so the caller skips the turn)."""
        import os
        from opendot.providers import env_var_for

        var = env_var_for(self.agent.config.model)
        if not var or os.environ.get(var):
            return False
        self._write(Text.assemble(
            ("no API key for ", "yellow"), (self.agent.config.model, "bold yellow"), ("\n", ""),
            (f"Set {var}, or run ", "dim"), ("/provider", "bold"),
            (" to paste one. Keys: github.com/vedaant00/opendot#any-model", "dim"),
        ), "err")
        return True

    def _slash(self, text: str) -> None:
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.lower()
        a = self.agent
        if cmd == "help":
            self._write("commands: /log /undo [id] /clear /compact /model /provider /mcp /composio /help", "sys")
        elif cmd == "clear":
            a.reset()
            self._clear_transcript()
            self._write("cleared — screen and conversation reset", "sys")
        elif cmd == "compact":
            n = a.compact()
            self._write(f"compacted: dropped {n} old message(s)", "sys")
        elif cmd == "model":
            # A bare arg sets the model directly; otherwise open the picker.
            if rest.strip():
                self._set_model(rest.strip())
            else:
                self.run_worker(self._pick_model(), exclusive=False)
        elif cmd in ("provider", "connect"):
            self.run_worker(self._connect_provider(), exclusive=False)
        elif cmd == "mcp":
            self.run_worker(self._manage_mcp(), exclusive=False)
        elif cmd == "composio":
            self.run_worker(self._manage_composio(), exclusive=False)
        elif cmd == "log":
            self.action_log()
        elif cmd == "undo":
            self._do_undo(rest.strip() or None)
        else:
            self._write(f"unknown command: /{cmd}", "sys")

    # -- model / provider pickers --
    def _set_model(self, model: str) -> None:
        """Switch the live model and refresh the sidebar (which shows it)."""
        self.agent.config.model = model
        self._write(f"model → {model}", "sys")
        self._refresh_sidebar()
        # Nudge if the key for this model isn't set yet.
        from opendot.providers import env_var_for
        import os
        var = env_var_for(model)
        if var and not os.environ.get(var):
            self._write(f"note: {var} is not set — use /provider to connect.", "sys")

    async def _pick_model(self) -> None:
        from opendot.providers import list_models, provider_of

        models = list_models()
        if not models:
            self._write("model list unavailable (litellm registry not found); "
                        "use  /model <id>  to set one directly.", "sys")
            return
        # (value, label, group) sorted by provider then model for grouped display.
        items = sorted(
            ((m, m, provider_of(m)) for m in models),
            key=lambda t: (t[2], t[1]),
        )
        chosen = await self.push_screen_wait(SearchListModal("Select model", items))
        if chosen:
            self._set_model(chosen)

    async def _connect_provider(self) -> None:
        from opendot.providers import CONNECTABLE_PROVIDERS, register_key

        items = [(var, name, "Providers") for name, var in CONNECTABLE_PROVIDERS]
        var = await self.push_screen_wait(SearchListModal("Connect a provider", items))
        if not var:
            return
        name = next((n for n, v in CONNECTABLE_PROVIDERS if v == var), var)
        key = await self.push_screen_wait(ApiKeyModal(name, var))
        if not key:
            return
        register_key(var, key)
        self._write(f"✓ {name} connected for this session.", "sys")
        self._write(f"to persist, add to your shell:  export {var}=…", "sys")
        self._refresh_sidebar()

    async def _manage_mcp(self) -> None:
        from opendot.mcp import add_mcp_server, load_mcp_config

        servers = load_mcp_config()
        mgr = getattr(self.agent, "mcp", None)
        connected = set(mgr.connected) if mgr else set()
        errors = dict(mgr.errors) if mgr else {}

        # Build the list: each server with a status glyph, then an Add entry.
        items: list[tuple[str, str, str]] = []
        for name, spec in servers.items():
            target = spec.get("url") or " ".join(
                [spec.get("command", "")] + spec.get("args", [])
            )
            if name in connected:
                n = sum(1 for mt in mgr.tools if mt.server == name)
                status = f"✓ {n} tools"
            elif name in errors:
                status = "✗ failed"
            else:
                status = "· not connected"
            items.append((f"server:{name}", f"{name}   {target[:40]}   {status}", "Servers"))
        items.append(("__add__", "➕ Add a server…", ""))

        chosen = await self.push_screen_wait(SearchListModal("MCP servers", items))
        if chosen != "__add__":
            return  # selecting a server is view-only for now
        result = await self.push_screen_wait(McpAddModal())
        if not result:
            return
        add_mcp_server(result["name"], result["spec"])
        self._write(
            f"✓ added MCP server '{result['name']}' — it connects on next launch "
            f"(restart opendot).",
            "sys",
        )

    async def _manage_composio(self) -> None:
        import asyncio
        import webbrowser

        from opendot import composio_tools as cx

        if not cx.composio_available():
            self._write("Composio isn't installed. Run:  pip install 'opendot[composio]'", "sys")
            return

        # First run (or no key yet): ask for the Composio API key.
        if not cx.is_configured():
            key = await self.push_screen_wait(ApiKeyModal("Composio", "COMPOSIO_API_KEY"))
            if not key:
                return
            cx.set_api_key(key)
            self._write("✓ Composio connected. Run /composio again to browse apps.", "sys")
            self._refresh_sidebar()
            return

        # Configured: list apps (marking connected/enabled ones), let the user pick.
        self._write("loading Composio apps…", "sys")
        apps = await asyncio.to_thread(cx.list_apps)
        if not apps:
            self._write("couldn't load Composio apps (check your key / connection).", "sys")
            return
        connected = await asyncio.to_thread(cx.list_connected)
        enabled = set(cx.enabled_apps())

        # Sort enabled apps to the top so they're easy to find/disable.
        apps.sort(key=lambda a: (a["slug"] not in enabled, a["name"].lower()))
        items: list[tuple[str, str, str, str]] = []
        for app in apps:
            slug = app["slug"]
            status = "✓ enabled" if slug in enabled else ("· connected" if slug in connected else "")
            label = f"{app['name']}   {slug}"
            items.append((slug, label, "Apps", status))

        slug = await self.push_screen_wait(SearchListModal("Composio apps", items))
        if not slug:
            return

        # Already enabled → offer to disable it (remove from opendot's tool set).
        if slug in enabled:
            choice = await self.push_screen_wait(SearchListModal(
                f"{slug} — enabled",
                [("disable", f"Disable {slug} in opendot", "Manage"),
                 ("keep", "Keep it enabled", "Manage")],
            ))
            if choice == "disable":
                cx.disable_app(slug)
                self._write(
                    f"✓ {slug} disabled. Its tools stop loading on next launch "
                    f"(restart opendot).",
                    "sys",
                )
                self._refresh_sidebar()
            return

        # Connect (OAuth → browser + wait, or direct connector → immediate).
        self._write(f"connecting {slug}…", "sys")
        res = await asyncio.to_thread(cx.begin_connect, slug)
        if res.error:
            self._write(f"couldn't connect {slug}: {res.error}", "sys")
            return
        if res.needs_auth and res.redirect_url:
            self._write(f"→ opening your browser to authorize {slug}…", "sys")
            webbrowser.open(res.redirect_url)
            self._write(f"  if it didn't open: {res.redirect_url}", "sys")
            try:
                await asyncio.to_thread(res.request.wait_for_connection, 180)
            except Exception as exc:  # noqa: BLE001
                self._write(f"authorization didn't complete: {exc}", "sys")
                return
        cx.add_enabled_app(slug)
        self._write(
            f"✓ {slug} connected and enabled. Its tools load on next launch "
            f"(restart opendot).",
            "sys",
        )
        self._refresh_sidebar()

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
                        self._write(Text("Thought", style="dim bold"), "think")
                    self._write(Text(ev.text.rstrip(), style="dim italic"), "think")
                elif ev.type == "text":
                    mode = "answer"
                    buf.append(ev.text)
                elif ev.type == "tool_start":
                    flush_answer(); mode = None
                    args = "  ".join(str(v)[:50] for v in ev.args.values())
                    line = Text("→ ", style="dim").append(ev.tool, style="bold")
                    if args:
                        line.append("  " + args, style="dim")
                    self._write(line, "tool")
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
