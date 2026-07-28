"""Modal screens: the irreversible-action confirm, the searchable list picker
(used by /model, /provider, /mcp, /composio), the API-key prompt, and the
add-MCP-server form."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from opendot.tui.helpers import _title_bar, _row_bar


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
        row_index = 0          # index into the OptionList (headers included)
        first_selectable = None  # index of the first non-disabled (data) row
        for item in self._items:
            value, label, group = item[0], item[1], item[2]
            status = item[3] if len(item) > 3 else ""  # optional right-aligned status
            if q and q not in label.lower():
                continue
            if group and group != last_group:
                ol.add_option(Option(Text(group.upper(), style="bold magenta"), disabled=True))
                last_group = group
                row_index += 1
            # Status (e.g. "✓ enabled") is rendered flush-right via a grid.
            prompt = _row_bar(label, status, "green") if status else Text(label)
            ol.add_option(Option(prompt, id=str(len(self._values))))
            if first_selectable is None:
                first_selectable = row_index  # this data row's OptionList index
            row_index += 1
            self._values.append(value)
        if first_selectable is not None:
            ol.highlighted = first_selectable

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
