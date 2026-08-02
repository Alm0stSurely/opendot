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

from dataclasses import dataclass, field

from opendot.reversibility import ledger, snapshots
from opendot.reversibility.ledger import ActionKind, LedgerEntry
from opendot.reversibility.snapshots import IgnoreRules, project_id_for


@dataclass
class Reversibility:
    workdir: str
    rules: IgnoreRules
    enabled: bool = True
    # The model + sampling params driving the session, stamped into every ledger
    # entry for a fully auditable trail. Set by the agent loop; empty for direct
    # CLI actions with no model behind them.
    model: str = ""
    params: dict = field(default_factory=dict)
    # Lockfiles changed by the most recent undo (see undo_last). Lets the UI warn
    # that the environment isn't restored until the package manager is re-run.
    last_changed_lockfiles: list[str] = field(default_factory=list)

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
        snapshot: bool = True,
    ) -> str:
        """Snapshot the workspace and log the action about to happen.

        When ``snapshot`` is False, the action is still logged (for the audit
        trail) but no snapshot is taken — so nothing is captured into the store.
        Used for the OPENDOT_NO_SNAPSHOT escape hatch, where the whole point is to
        NOT keep a recoverable copy (e.g. shredding a secret). Such an action is
        inherently not reversible.

        Returns the snapshot id (== ledger entry id). No-op returns "" if disabled.
        """
        if not self.enabled:
            return ""
        if not snapshot:
            # No snapshot is taken, so derive a sortable id from the shared
            # action-id counter (snapshots + ledger both feed it), keeping ids
            # unique and monotonic even when snapshot and no-snapshot actions mix.
            entry_id = f"{snapshots.max_action_id(self.project_id) + 1:06d}"
            ledger.append(
                self.project_id,
                LedgerEntry(
                    id=entry_id,
                    kind=kind,
                    detail=detail,
                    snapshot_before="",  # no snapshot exists to restore
                    reversible=False,
                    note=note,
                    timestamp=timestamp,
                    model=self.model,
                    params=dict(self.params),
                ),
            )
            return entry_id
        snap = snapshots.take_snapshot(self.workdir, self.rules)
        # If files were too large to snapshot, this action can't be fully undone —
        # record that honestly in the ledger note.
        if snap.skipped_large:
            n = len(snap.skipped_large)
            big_note = f"{n} file(s) too large to snapshot — changes to them can't be undone"
            note = f"{note}; {big_note}" if note else big_note
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
                model=self.model,
                params=dict(self.params),
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

    def restore_to(self, snapshot_id: str) -> list[str]:
        """Restore the workspace to the state captured in ``snapshot_id``.

        Returns any dependency lockfiles the restore changed — the caller should
        warn that the environment isn't restored until the package manager is
        re-run (restoring a lockfile rolls back declared versions, not installed
        packages).
        """
        snap = snapshots.load_snapshot(self.project_id, snapshot_id)
        return snapshots.restore_snapshot(snap, self.rules)

    def undo_last(self) -> LedgerEntry | None:
        """Revert the most recent action (restore its before-snapshot).

        Returns the entry that was undone, or None if there's nothing to undo.
        Any dependency lockfiles the restore changed are left in
        ``last_changed_lockfiles`` for the caller to warn about.
        """
        self.last_changed_lockfiles = []
        entries = self.history()
        if not entries:
            return None
        last_entry = entries[-1]
        if not last_entry.snapshot_before:
            # A no-snapshot action (OPENDOT_NO_SNAPSHOT) has nothing to restore.
            return None
        self.last_changed_lockfiles = self.restore_to(last_entry.snapshot_before)
        return last_entry
