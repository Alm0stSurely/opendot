"""The action ledger — an append-only, auditable record of everything opendot did.

One JSONL line per action, per project, at ``~/.opendot/ledger/<project>.jsonl``.
Each entry ties an action (a file write or a shell command) to the snapshot
taken *before* it ran, plus whether it was reversible. This is what powers
``opendot log`` (the audit trail) and ``opendot undo`` (restore to a point), and
it's the "you can see exactly what it did" half of the trust story.

Timestamps are stamped by the caller (the core avoids wall-clock so it stays
deterministic/testable); callers pass a timestamp string or leave it blank.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

from opendot.reversibility.snapshots import store_root

ActionKind = Literal["write", "shell"]


@dataclass
class LedgerEntry:
    id: str  # matches the snapshot id taken before this action
    kind: ActionKind
    detail: str  # path written, or the shell command
    snapshot_before: str  # snapshot id capturing state BEFORE the action
    reversible: bool = True
    note: str = ""  # e.g. "escapes workspace: git push"
    timestamp: str = ""
    # Which model + sampling params drove this action, for a fully auditable trail
    # ("what did it do" now includes "which model, with what settings"). Both
    # optional and default-empty so ledgers written before this field still load,
    # and non-agent callers (CLI direct actions) can omit them.
    model: str = ""
    params: dict = field(default_factory=dict)


def _ledger_path(project_id: str) -> Path:
    d = store_root() / "ledger"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{project_id}.jsonl"


def append(project_id: str, entry: LedgerEntry) -> None:
    with _ledger_path(project_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")


def read_all(project_id: str) -> list[LedgerEntry]:
    path = _ledger_path(project_id)
    if not path.exists():
        return []
    known = {f.name for f in fields(LedgerEntry)}
    entries: list[LedgerEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # Keep only fields this version knows about, so ledgers written by an
        # older opendot (missing model/params) or a newer one (extra keys) both
        # load. Missing fields fall back to the dataclass defaults.
        raw = {k: v for k, v in json.loads(line).items() if k in known}
        entries.append(LedgerEntry(**raw))
    return entries


def last(project_id: str) -> LedgerEntry | None:
    entries = read_all(project_id)
    return entries[-1] if entries else None


def clear(project_id: str) -> int:
    """Delete this project's ledger file. Returns how many entries were removed.
    Note: this discards the undo history; snapshot objects on disk are left
    (harmless, content-addressed) but are no longer referenced by any entry."""
    path = _ledger_path(project_id)
    n = len(read_all(project_id))
    if path.exists():
        path.unlink()
    return n
