"""The undo/redo cursor — a small sidecar next to the append-only ledger.

The ledger is an immutable audit trail: it records what opendot *did*, and
undoing something is not itself an action worth recording. So the cursor
tracking "how far back have we walked" lives here instead, at
``~/.opendot/redo/<project>.json``, and is safe to lose (losing it just means
you can't redo).

Two fields do the work:

``undone``
    How many trailing ledger actions are currently reverted. 0 means the
    workspace is at the head of the history.
``head_snapshot``
    The workspace as it was just before the *first* undo of a run. Every other
    "state after action N" is already recoverable — it is the snapshot the next
    action took before it ran — but the head has no next action, so it would
    otherwise be lost the moment you undo.

``ledger_len`` is a staleness guard. If the ledger is no longer the length it
was when the cursor was written, the cursor describes a history that no longer
exists and is discarded rather than trusted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from opendot.reversibility.snapshots import store_root


@dataclass
class RedoState:
    undone: int = 0
    head_snapshot: str = ""
    ledger_len: int = 0

    @property
    def can_redo(self) -> bool:
        return self.undone > 0


def _path(project_id: str) -> Path:
    d = store_root() / "redo"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{project_id}.json"


def read(project_id: str, ledger_len: int) -> RedoState:
    """Load the cursor, or a fresh one if it is missing, corrupt, or stale.

    ``ledger_len`` is the current ledger length; a mismatch means actions were
    appended or the ledger was cleared since the cursor was written, so the
    saved position no longer refers to the same history.
    """
    p = _path(project_id)
    if not p.exists():
        return RedoState(ledger_len=ledger_len)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        state = RedoState(
            undone=int(raw.get("undone", 0)),
            head_snapshot=str(raw.get("head_snapshot", "")),
            ledger_len=int(raw.get("ledger_len", 0)),
        )
    except (json.JSONDecodeError, TypeError, ValueError, OSError):
        # A hand-edited or truncated sidecar must not break undo.
        return RedoState(ledger_len=ledger_len)
    if state.ledger_len != ledger_len or not 0 <= state.undone <= ledger_len:
        return RedoState(ledger_len=ledger_len)
    return state


def write(project_id: str, state: RedoState) -> None:
    _path(project_id).write_text(json.dumps(asdict(state)), encoding="utf-8")


def clear(project_id: str) -> None:
    """Drop the redo stack. Called whenever a new action makes it meaningless."""
    p = _path(project_id)
    if p.exists():
        p.unlink()
