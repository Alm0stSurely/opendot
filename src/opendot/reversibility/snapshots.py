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

# Files larger than this are NOT snapshotted (skipped), so a giant file can't
# make every snapshot slow and bloat the store. An action touching such a file
# is therefore not fully undoable — take_snapshot reports the skipped paths so
# the caller can say so.
_MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


@dataclass
class FileEntry:
    """One captured file: content hash, POSIX mode bits, and (mtime, size) so a
    later snapshot can skip re-hashing an unchanged file."""
    h: str
    mode: int | None = None   # st_mode & 0o777, or None if unknown (old snapshots)
    mtime: float | None = None
    size: int | None = None


@dataclass
class Snapshot:
    id: str
    project_id: str
    workdir: str
    files: dict[str, FileEntry]  # relative posix path -> FileEntry
    skipped_large: list[str] = field(default_factory=list)  # paths too big to capture


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
    """Capture the workspace state. Cheap when little changed (content-addressed).

    Files over ``_MAX_FILE_BYTES`` are skipped (recorded in ``skipped_large``) so
    one huge file can't make snapshots slow/bloated — those are not undoable.
    """
    rules = rules or IgnoreRules()
    wd = Path(workdir).resolve()
    pid = project_id_for(wd)

    # Previous snapshot's entries, keyed by path — used to skip re-hashing files
    # whose (mtime, size) are unchanged. This is what makes snapshots of large,
    # mostly-static workspaces near-instant.
    prev = _latest_snapshot_files(pid)

    files: dict[str, FileEntry] = {}
    skipped_large: list[str] = []
    for f in _iter_files(wd, rules):
        rel = f.relative_to(wd).as_posix()
        try:
            st = f.stat()
        except OSError:
            continue
        mode = st.st_mode & 0o777
        # Fast path: unchanged since the last snapshot → reuse its hash, no read.
        old = prev.get(rel)
        if (old is not None and old.mtime == st.st_mtime and old.size == st.st_size
                and old.h):
            files[rel] = FileEntry(h=old.h, mode=mode, mtime=st.st_mtime, size=st.st_size)
            continue
        if st.st_size > _MAX_FILE_BYTES:
            skipped_large.append(rel)
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue  # unreadable file: skip rather than fail the whole snapshot
        h = _write_object(data)
        files[rel] = FileEntry(h=h, mode=mode, mtime=st.st_mtime, size=st.st_size)

    snap_id = _next_snapshot_id(pid)
    snap = Snapshot(id=snap_id, project_id=pid, workdir=str(wd),
                    files=files, skipped_large=skipped_large)
    _write_manifest(snap)

    # Bound disk growth: drop this project's oldest snapshots beyond the
    # retention limit, then delete any objects no longer referenced anywhere.
    if prune_project_snapshots(pid):
        gc_objects()
    return snap


def _latest_snapshot_files(project_id: str) -> dict[str, FileEntry]:
    """The file map of the most recent snapshot for this project, for the
    unchanged-file fast path. Empty if none / unreadable."""
    ids = list_snapshots(project_id)
    if not ids:
        return {}
    try:
        return load_snapshot(project_id, ids[-1]).files
    except Exception:  # noqa: BLE001
        return {}


# How many snapshots to keep per project before older ones are pruned. Bounds
# disk growth on long, edit-heavy sessions. Content is deduped across snapshots,
# so this is generous.
MAX_SNAPSHOTS_PER_PROJECT = 50


def _referenced_hashes() -> set[str]:
    """Every content hash referenced by ANY surviving snapshot, across ALL
    projects. Objects are shared globally (deduped), so GC must consider every
    project's manifests before deleting an object."""
    refs: set[str] = set()
    snaps_root = store_root() / "snapshots"
    if not snaps_root.exists():
        return refs
    for proj_dir in snaps_root.iterdir():
        if not proj_dir.is_dir():
            continue
        for manifest in proj_dir.glob("*.json"):
            try:
                d = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                # Unreadable manifest → be conservative, keep its objects by
                # skipping (we simply can't enumerate them, so we won't GC them
                # blindly — but we also can't add refs; safest is to abort GC).
                raise
            for v in d.get("files", {}).values():
                h = v if isinstance(v, str) else v.get("h")
                if h:
                    refs.add(h)
    return refs


def prune_project_snapshots(project_id: str, keep: int | None = None) -> int:
    """Delete this project's oldest snapshot manifests beyond ``keep`` (default:
    MAX_SNAPSHOTS_PER_PROJECT, read at call time). Returns how many manifests
    were removed. Objects are freed separately by gc_objects."""
    if keep is None:
        keep = MAX_SNAPSHOTS_PER_PROJECT
    ids = list_snapshots(project_id)
    if len(ids) <= keep:
        return 0
    to_drop = ids[:len(ids) - keep]  # ids are zero-padded, sorted oldest→newest
    d = _snapshots_dir(project_id)
    removed = 0
    for sid in to_drop:
        try:
            (d / f"{sid}.json").unlink()
            removed += 1
        except OSError:
            pass
    return removed


def gc_objects() -> int:
    """Delete stored objects not referenced by any surviving snapshot (across
    all projects). Returns the number of objects removed. Aborts safely (returns
    0) if any manifest is unreadable, so we never delete a still-referenced blob."""
    try:
        refs = _referenced_hashes()
    except Exception:  # noqa: BLE001 - unreadable manifest: don't risk deleting live data
        return 0
    objects = _objects_dir()
    removed = 0
    for obj in objects.iterdir():
        if obj.suffix == ".tmp":
            continue
        if obj.name not in refs:
            try:
                obj.unlink()
                removed += 1
            except OSError:
                pass
    return removed


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
            {"id": snap.id, "project_id": snap.project_id, "workdir": snap.workdir,
             "files": {rel: {"h": e.h, "m": e.mode, "t": e.mtime, "s": e.size}
                       for rel, e in snap.files.items()},
             "skipped_large": snap.skipped_large},
            indent=0,
        ),
        encoding="utf-8",
    )


def load_snapshot(project_id: str, snap_id: str) -> Snapshot:
    path = _snapshots_dir(project_id) / f"{snap_id}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    files: dict[str, FileEntry] = {}
    for rel, v in d["files"].items():
        # Back-compat: old manifests stored a bare hash string (no metadata).
        if isinstance(v, str):
            files[rel] = FileEntry(h=v, mode=None)
        else:
            files[rel] = FileEntry(h=v["h"], mode=v.get("m"),
                                   mtime=v.get("t"), size=v.get("s"))
    return Snapshot(id=d["id"], project_id=d["project_id"], workdir=d["workdir"],
                    files=files, skipped_large=d.get("skipped_large", []))


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

    # 1. Restore all files from the manifest (content + permission mode).
    for rel, entry in snap.files.items():
        target = wd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_read_object(entry.h))
        if entry.mode is not None:
            try:
                target.chmod(entry.mode)
            except OSError:
                pass  # best-effort: FS may not support the mode

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
