"""opendot's full-screen Textual TUI.

Split across submodules for readability:
  - app.py       the OpendotTUI App + run_tui entry point
  - modals.py    confirm / searchable-list / api-key / add-MCP modals
  - sidebar.py   the reversibility-ledger sidebar
  - commands.py  the slash-command registry
  - helpers.py   shared row/render/context helpers + the logo path
"""

from opendot.tui.app import OpendotTUI, run_tui
from opendot.tui.modals import (
    ApiKeyModal, ConfirmModal, McpAddModal, SearchListModal,
)

__all__ = [
    "OpendotTUI", "run_tui",
    "ApiKeyModal", "ConfirmModal", "McpAddModal", "SearchListModal",
]
