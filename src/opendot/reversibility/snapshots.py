"""Content-addressed snapshot store — the foundation of opendot's reversibility.

A snapshot captures the state of a workspace (a directory subtree) at a point in
time. Files are stored *once* by content hash under ``~/.opendot/objects/``, so
repeated snapshots are cheap: only changed files add new objects. A snapshot
itself is a small manifest (path -> hash) under
``~/.opendot/snapshots/<project>/<id>.json``.

Restoring a snapshot makes the workspace byte-for-byte match the manifest:
files are rewritten from their stored objects, and files that exist now but were
not in the snapshot are removed. This is the "walk it back" guarantee — and it
must be exact, so it is covered by round-trip tests.

Ignore rules keep snapshots small and relevant. Defaults skip VCS/dep/build
dirs; callers may override in both directions (see ``IgnoreRules``).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Store location
# ---------------------------------------------------------------------------

def store_root() -> Path:
    """Root of the global opendot store (override with OPENDOT_HOME, for tests)."""
    root = Path(os.environ.get("OPENDOT_HOME", Path.home() / ".opendot"))
    return root


def _objects_dir() -> Path:
    d = store_root() / "objects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshots_dir(project_id: str) -> Path:
    d = store_root() / "snapshots" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_id_for(workdir: str | Path) -> str:
    """Stable per-workspace id (hash of the absolute path)."""
    p = str(Path(workdir).resolve())
    return hashlib.sha256(p.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Ignore rules
# ---------------------------------------------------------------------------

_DEFAULT_IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__",
                        ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
                        ".opendot", ".DS_Store"}


@dataclass
class IgnoreRules:
    """What to skip when snapshotting.

    ``skip_dirs`` are directory *names* skipped anywhere in the tree. ``force_include``
    are directory names to snapshot even if they're in the default skip set — this
    is how OPENDOT.md's "snapshot venv/node_modules" override works. ``extra_skip``
    adds user-specified skips. ``force_include`` wins over skips.
    """

    force_include: set[str] = field(default_factory=set)
    extra_skip: set[str] = field(default_factory=set)

    def skipped(self, name: str) -> bool:
        if name in self.force_include:
            return False
        return name in (_DEFAULT_IGNORE_DIRS | self.extra_skip)


# ---------------------------------------------------------------------------
# Hashing / object storage
# ---------------------------------------------------------------------------

def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_object(data: bytes) -> str:
    """Store bytes by content hash; return the hash. Idempotent."""
    h = _hash_bytes(data)
    obj = _objects_dir() / h
    if not obj.exists():
        tmp = obj.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(obj)  # atomic
    return h


def _read_object(h: str) -> bytes:
    return (_objects_dir() / h).read_bytes()


# ---------------------------------------------------------------------------
# Snapshot / restore
# ---------------------------------------------------------------------------

@dataclass
class Snapshot:
    id: str
    project_id: str
    workdir: str
    files: dict[str, str]  # relative posix path -> content hash


def _iter_files(workdir: Path, rules: IgnoreRules):
    """Yield files under workdir, honoring ignore rules (dir-name based)."""
    for dirpath, dirnames, filenames in os.walk(workdir):
        # prune skipped dirs in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if not rules.skipped(d)]
        for fn in filenames:
            if rules.skipped(fn):
                continue
            full = Path(dirpath) / fn
            if full.is_symlink() or not full.is_file():
                continue
            yield full


def take_snapshot(workdir: str | Path, rules: IgnoreRules | None = None) -> Snapshot:
    """Capture the workspace state. Cheap when little changed (content-addressed)."""
    rules = rules or IgnoreRules()
    wd = Path(workdir).resolve()
    pid = project_id_for(wd)
    files: dict[str, str] = {}
    for f in _iter_files(wd, rules):
        try:
            data = f.read_bytes()
        except OSError:
            continue  # unreadable file: skip rather than fail the whole snapshot
        h = _write_object(data)
        rel = f.relative_to(wd).as_posix()
        files[rel] = h

    snap_id = _next_snapshot_id(pid)
    snap = Snapshot(id=snap_id, project_id=pid, workdir=str(wd), files=files)
    _write_manifest(snap)
    return snap


def _next_snapshot_id(project_id: str) -> str:
    """Monotonic, sortable id. Counter persisted per project (no wall-clock dep)."""
    d = _snapshots_dir(project_id)
    existing = sorted(int(p.stem) for p in d.glob("*.json") if p.stem.isdigit())
    n = (existing[-1] + 1) if existing else 1
    return f"{n:06d}"


def _write_manifest(snap: Snapshot) -> None:
    path = _snapshots_dir(snap.project_id) / f"{snap.id}.json"
    path.write_text(
        json.dumps(
            {"id": snap.id, "project_id": snap.project_id,
             "workdir": snap.workdir, "files": snap.files},
            indent=0,
        ),
        encoding="utf-8",
    )


def load_snapshot(project_id: str, snap_id: str) -> Snapshot:
    path = _snapshots_dir(project_id) / f"{snap_id}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(id=d["id"], project_id=d["project_id"],
                    workdir=d["workdir"], files=d["files"])


def list_snapshots(project_id: str) -> list[str]:
    d = _snapshots_dir(project_id)
    return sorted(p.stem for p in d.glob("*.json") if p.stem.isdigit())


def restore_snapshot(snap: Snapshot, rules: IgnoreRules | None = None) -> None:
    """Make the workspace byte-for-byte match the snapshot.

    Rewrites/creates every file in the manifest; removes files that exist now but
    were not captured (respecting ignore rules — we never touch skipped paths).
    """
    rules = rules or IgnoreRules()
    wd = Path(snap.workdir).resolve()

    # 1. Restore all files from the manifest.
    for rel, h in snap.files.items():
        target = wd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_read_object(h))

    # 2. Remove files present now but absent from the snapshot (only non-skipped).
    manifest_paths = set(snap.files)
    for f in _iter_files(wd, rules):
        rel = f.relative_to(wd).as_posix()
        if rel not in manifest_paths:
            try:
                f.unlink()
            except OSError:
                pass

    # 3. Prune now-empty directories (best effort, bottom-up).
    for dirpath, dirnames, filenames in os.walk(wd, topdown=False):
        p = Path(dirpath)
        if p == wd:
            continue
        if rules.skipped(p.name):
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass
