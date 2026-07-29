"""Shared TUI helpers: layout-row builders, tool-result rendering, the logo
path, and the models-context lookup. Kept dependency-light so both the modals
and the app can import from here."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

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
# This file lives at src/opendot/tui/helpers.py, so the repo root is three
# parents up from the package dir (src/opendot -> src -> repo root).
_LOGO_PATH = Path(__file__).resolve().parents[3] / "assets" / "logo-full.png"


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


def _render_composio_result(result: str):
    """Compact one-line status for a Composio tool result, instead of dumping
    the raw JSON. Falls back to None if we can't parse it (caller shows the
    generic preview)."""
    import json

    try:
        data = json.loads(result)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    # Execute result: {"total_count", "success_count", "error_count", ...}
    if "success_count" in data or "total_count" in data:
        ok = int(data.get("success_count", 0))
        total = int(data.get("total_count", ok))
        style = "green" if ok and ok == total else ("yellow" if ok else "red")
        return Text(f"✓ executed · {ok}/{total} ok", style=style)
    # Search result: {"results": [ ... ]}
    if isinstance(data.get("results"), list):
        n = len(data["results"])
        return Text(f"found {n} tool{'s' if n != 1 else ''}", style="dim")
    return None


def _render_tool_result(tool: str, result: str):
    """Render a tool's result. File edits (write_file/edit) show a colored diff;
    Composio results show a compact status; other tools show a short preview.
    This is the 'see exactly what changed' transparency that reinforces
    reversibility."""
    result = result.rstrip()
    if tool.startswith("composio__"):
        compact = _render_composio_result(result)
        if compact is not None:
            return compact
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
    # non-diff: a short preview, dimmed. Cap BOTH lines and total length —
    # tool results (e.g. Composio search) are often one huge single-line JSON
    # blob, which line-only truncation wouldn't shorten.
    lines = result.splitlines() or ["(done)"]
    preview = "\n".join(lines[:3])
    extra_lines = len(lines) - 3
    _MAX = 500
    if len(preview) > _MAX:
        preview = preview[:_MAX].rstrip() + " …"
    elif extra_lines > 0:
        preview += f"\n  … (+{extra_lines} more lines)"
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
