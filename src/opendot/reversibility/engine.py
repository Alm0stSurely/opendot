"""The reversibility engine — ties snapshots + ledger together.

This is the moat. Before any mutating action (a file write or a shell command),
``before_action`` snapshots the workspace and records a ledger entry tying that
action to its pre-state. ``undo`` / ``restore_to`` walk the workspace back to a
recorded point, exactly (byte-for-byte), and honestly (never touching ignored
paths, never claiming to reverse what it can't).

The classifier (which decides whether a shell command is reversible / needs
confirmation) lives in ``classifier.py`` and is consulted by the caller; this
module just records the ``reversible`` flag it's given.
"""

from __future__ import annotations

from dataclasses import dataclass

from opendot.reversibility import ledger, snapshots
from opendot.reversibility.ledger import ActionKind, LedgerEntry
from opendot.reversibility.snapshots import IgnoreRules, project_id_for


@dataclass
class Reversibility:
    workdir: str
    rules: IgnoreRules
    enabled: bool = True

    @property
    def project_id(self) -> str:
        return project_id_for(self.workdir)

    def before_action(
        self,
        kind: ActionKind,
        detail: str,
        *,
        reversible: bool = True,
        note: str = "",
        timestamp: str = "",
    ) -> str:
        """Snapshot the workspace and log the action about to happen.

        Returns the snapshot id (== ledger entry id). No-op returns "" if disabled.
        """
        if not self.enabled:
            return ""
        snap = snapshots.take_snapshot(self.workdir, self.rules)
        ledger.append(
            self.project_id,
            LedgerEntry(
                id=snap.id,
                kind=kind,
                detail=detail,
                snapshot_before=snap.id,
                reversible=reversible,
                note=note,
                timestamp=timestamp,
            ),
        )
        return snap.id

    # -- history / undo --
    def history(self) -> list[LedgerEntry]:
        return ledger.read_all(self.project_id)

    def clear_history(self) -> int:
        """Clear this project's action ledger (discards undo history). Returns
        the number of entries removed."""
        return ledger.clear(self.project_id)

    def restore_to(self, snapshot_id: str) -> None:
        """Restore the workspace to the state captured in ``snapshot_id``."""
        snap = snapshots.load_snapshot(self.project_id, snapshot_id)
        snapshots.restore_snapshot(snap, self.rules)

    def undo_last(self) -> LedgerEntry | None:
        """Revert the most recent action (restore its before-snapshot).

        Returns the entry that was undone, or None if there's nothing to undo.
        """
        entries = self.history()
        if not entries:
            return None
        last_entry = entries[-1]
        self.restore_to(last_entry.snapshot_before)
        return last_entry
