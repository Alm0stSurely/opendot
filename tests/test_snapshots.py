"""Round-trip tests for the snapshot store — the sacred foundation.

If any of these fail, opendot's core promise (undo that doesn't lie) is broken.
Uses an isolated OPENDOT_HOME so the real ~/.opendot store is never touched.
"""

import os
from pathlib import Path

import pytest

from opendot.reversibility import snapshots as S


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    yield


def _workspace(tmp_path) -> Path:
    wd = tmp_path / "ws"
    wd.mkdir()
    return wd


def test_basic_roundtrip(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "a.txt").write_text("hello")
    (wd / "b.py").write_text("print(1)\n")

    snap = S.take_snapshot(wd)

    # mutate
    (wd / "a.txt").write_text("CHANGED")
    (wd / "b.py").unlink()

    S.restore_snapshot(snap)
    assert (wd / "a.txt").read_text() == "hello"
    assert (wd / "b.py").read_text() == "print(1)\n"


def test_nested_dirs(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "src" / "pkg").mkdir(parents=True)
    (wd / "src" / "pkg" / "x.py").write_text("x = 1")
    snap = S.take_snapshot(wd)

    import shutil

    shutil.rmtree(wd / "src")  # delete the whole subtree
    assert not (wd / "src").exists()

    S.restore_snapshot(snap)
    assert (wd / "src" / "pkg" / "x.py").read_text() == "x = 1"


def test_binary_files(tmp_path):
    wd = _workspace(tmp_path)
    data = bytes(range(256)) * 4  # non-utf8 binary
    (wd / "blob.bin").write_bytes(data)
    snap = S.take_snapshot(wd)

    (wd / "blob.bin").write_bytes(b"corrupted")
    S.restore_snapshot(snap)
    assert (wd / "blob.bin").read_bytes() == data


def test_stored_object_survives_in_place_edit_of_source(tmp_path):
    """The store may clone (reflink) a file rather than copy it. That must be
    copy-on-write safe: editing the original file in place after the snapshot
    must NOT change the stored object, or undo would restore corrupted data."""
    wd = _workspace(tmp_path)
    original = b"first version" * 1000
    (wd / "f.bin").write_bytes(original)
    snap = S.take_snapshot(wd)

    # Overwrite in place (open+write, not atomic replace) — the worst case for
    # a naive hardlink. A reflink/copy must be unaffected.
    with open(wd / "f.bin", "r+b") as fh:
        fh.seek(0)
        fh.write(b"XXXX")

    S.restore_snapshot(snap)
    assert (wd / "f.bin").read_bytes() == original  # stored object was intact


def test_large_file_roundtrip_via_streaming(tmp_path):
    """A multi-chunk file (bigger than the streaming block) round-trips exactly."""
    wd = _workspace(tmp_path)
    data = bytes(range(256)) * (S._CHUNK // 128)  # a few MB, spans many chunks
    (wd / "big.bin").write_bytes(data)
    snap = S.take_snapshot(wd)
    (wd / "big.bin").write_bytes(b"nope")
    S.restore_snapshot(snap)
    assert (wd / "big.bin").read_bytes() == data


def test_empty_file(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "empty").write_text("")
    snap = S.take_snapshot(wd)
    (wd / "empty").write_text("not empty")
    S.restore_snapshot(snap)
    assert (wd / "empty").read_text() == ""


def test_new_file_is_removed_on_restore(tmp_path):
    """A file the agent CREATED after the snapshot must be gone after undo."""
    wd = _workspace(tmp_path)
    (wd / "keep.txt").write_text("keep")
    snap = S.take_snapshot(wd)

    (wd / "agent_made_this.txt").write_text("oops")  # agent creates a new file
    assert (wd / "agent_made_this.txt").exists()

    S.restore_snapshot(snap)
    assert not (wd / "agent_made_this.txt").exists()  # cleanly removed
    assert (wd / "keep.txt").read_text() == "keep"


def test_rename_roundtrip(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "old.txt").write_text("data")
    snap = S.take_snapshot(wd)

    (wd / "old.txt").rename(wd / "new.txt")  # rename = delete old + create new
    S.restore_snapshot(snap)
    assert (wd / "old.txt").read_text() == "data"
    assert not (wd / "new.txt").exists()


def test_content_addressing_dedupes(tmp_path):
    """Identical content across files stores one object; unchanged files add none."""
    wd = _workspace(tmp_path)
    (wd / "a").write_text("same")
    (wd / "b").write_text("same")
    S.take_snapshot(wd)
    objs_after_first = len(list((S.store_root() / "objects").iterdir()))

    # snapshot again with no changes -> no new objects
    S.take_snapshot(wd)
    objs_after_second = len(list((S.store_root() / "objects").iterdir()))
    assert objs_after_second == objs_after_first
    # "same" content dedupes to a single object (plus none else here)
    assert objs_after_first == 1


def test_ignore_defaults_skip_git_and_node_modules(tmp_path):
    wd = _workspace(tmp_path)
    (wd / ".git").mkdir()
    (wd / ".git" / "HEAD").write_text("ref")
    (wd / "node_modules").mkdir()
    (wd / "node_modules" / "dep.js").write_text("x")
    (wd / "real.py").write_text("real")

    snap = S.take_snapshot(wd)
    assert "real.py" in snap.files
    assert not any(p.startswith(".git") for p in snap.files)
    assert not any(p.startswith("node_modules") for p in snap.files)


def test_force_include_overrides_default_skip(tmp_path):
    """OPENDOT.md 'snapshot node_modules' override must win."""
    wd = _workspace(tmp_path)
    (wd / "node_modules").mkdir()
    (wd / "node_modules" / "dep.js").write_text("x")

    rules = S.IgnoreRules(force_include={"node_modules"})
    snap = S.take_snapshot(wd, rules)
    assert any(p.startswith("node_modules") for p in snap.files)


def test_restore_never_touches_skipped_paths(tmp_path):
    """Restore must not delete files inside ignored dirs (e.g. .git)."""
    wd = _workspace(tmp_path)
    (wd / "real.py").write_text("v1")
    snap = S.take_snapshot(wd)  # .git doesn't exist yet

    # after snapshot, a .git dir appears (like `git init`)
    (wd / ".git").mkdir()
    (wd / ".git" / "HEAD").write_text("precious")

    S.restore_snapshot(snap)
    # .git must be untouched even though it wasn't in the snapshot
    assert (wd / ".git" / "HEAD").read_text() == "precious"


def test_snapshot_ids_are_monotonic(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "f").write_text("a")
    s1 = S.take_snapshot(wd)
    (wd / "f").write_text("b")
    s2 = S.take_snapshot(wd)
    assert s1.id < s2.id
    assert S.list_snapshots(s1.project_id) == [s1.id, s2.id]


def test_load_snapshot_roundtrip(tmp_path):
    wd = _workspace(tmp_path)
    (wd / "f").write_text("data")
    s = S.take_snapshot(wd)
    loaded = S.load_snapshot(s.project_id, s.id)
    assert loaded.files == s.files
    assert loaded.workdir == s.workdir


def test_restore_brings_back_file_permissions(tmp_path):
    wd = _workspace(tmp_path)
    script = wd / "run.sh"
    script.write_text("echo hi")
    script.chmod(0o755)
    snap = S.take_snapshot(wd)

    script.chmod(0o644)  # someone chmods it
    S.restore_snapshot(S.load_snapshot(snap.project_id, snap.id))
    assert (os.stat(script).st_mode & 0o777) == 0o755  # mode restored


def test_large_files_are_skipped_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_MAX_FILE_BYTES", 1024)  # 1KB cap for the test
    wd = _workspace(tmp_path)
    (wd / "small.txt").write_text("ok")
    (wd / "big.bin").write_bytes(b"x" * 2048)  # over the cap
    snap = S.take_snapshot(wd)
    assert "small.txt" in snap.files
    assert "big.bin" not in snap.files
    assert snap.skipped_large == ["big.bin"]


def test_retention_prunes_old_snapshots_and_gcs_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_SNAPSHOTS_PER_PROJECT", 3)
    wd = _workspace(tmp_path)
    f = wd / "f.txt"
    snap = None
    for i in range(6):
        f.write_text(f"version-{i}")
        os.utime(f, (i + 1, i + 1))  # deterministic mtimes so each version differs
        snap = S.take_snapshot(wd)
    kept = S.list_snapshots(snap.project_id)
    assert len(kept) == 3  # only the newest 3 survive
    # orphaned objects from v0..v2 are GC'd; ~3 remain
    objs = [o for o in (S._objects_dir()).iterdir() if o.suffix != ".tmp"]
    assert len(objs) <= 4
    # the newest snapshot is still fully restorable
    f.write_text("wrecked")
    S.restore_snapshot(S.load_snapshot(snap.project_id, snap.id))
    assert f.read_text() == "version-5"


def test_gc_never_deletes_objects_shared_by_another_project(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_SNAPSHOTS_PER_PROJECT", 2)
    a = tmp_path / "A"
    a.mkdir()
    b = tmp_path / "B"
    b.mkdir()
    (a / "shared.txt").write_text("SHARED")
    (b / "shared.txt").write_text("SHARED")  # identical content → one object
    S.take_snapshot(a)
    b_snap = S.take_snapshot(b)
    # churn A past its retention so A's GC runs repeatedly
    for i in range(4):
        (a / "x").write_text(str(i))
        os.utime(a / "x", (i + 1, i + 1))
        S.take_snapshot(a)
    # B's shared object must survive (still referenced by B) and B must restore
    (b / "shared.txt").write_text("wrecked")
    S.restore_snapshot(S.load_snapshot(b_snap.project_id, b_snap.id))
    assert (b / "shared.txt").read_text() == "SHARED"


def test_unchanged_files_are_not_rehashed(tmp_path, monkeypatch):
    wd = _workspace(tmp_path)
    (wd / "a.txt").write_text("aaa")
    (wd / "b.txt").write_text("bbb")

    reads = {"n": 0}
    orig = S._write_object_from_path
    monkeypatch.setattr(
        S,
        "_write_object_from_path",
        lambda path: (reads.__setitem__("n", reads["n"] + 1), orig(path))[1],
    )

    S.take_snapshot(wd)
    reads["n"] = 0
    S.take_snapshot(wd)  # nothing changed
    assert reads["n"] == 0  # fast path: no re-reads

    import time

    time.sleep(0.01)
    (wd / "a.txt").write_text("changed")
    reads["n"] = 0
    S.take_snapshot(wd)
    assert reads["n"] == 1  # only the changed file re-hashed


def test_ledger_clear_wipes_history(tmp_path):
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))
    rev.before_action("write", "a.txt", reversible=True)
    rev.before_action("shell", "git init", reversible=False)
    assert len(rev.history()) == 2

    removed = rev.clear_history()
    assert removed == 2
    assert rev.history() == []
    # clearing an already-empty ledger is a no-op, not an error
    assert rev.clear_history() == 0


def test_undo_reports_changed_lockfile(tmp_path):
    """Undoing across a lockfile change must report it, so the caller can warn
    that the environment isn't restored (only the declared versions)."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    (wd / "package-lock.json").write_text('{"version": 1}')
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))

    rev.before_action("shell", "npm install foo", reversible=True)  # snapshot before
    (wd / "package-lock.json").write_text('{"version": 2}')  # "install" changes it

    undone = rev.undo_last()
    assert undone is not None
    assert (wd / "package-lock.json").read_text() == '{"version": 1}'  # rolled back
    assert rev.last_changed_lockfiles == ["package-lock.json"]  # and reported


def test_undo_reports_lockfile_created_by_install(tmp_path):
    """A lockfile the install *created* (absent in the snapshot) is removed by
    undo, and that also counts as a changed lockfile worth warning about."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    (wd / "app.py").write_text("x = 1")
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))

    rev.before_action("shell", "uv add requests", reversible=True)  # no lockfile yet
    (wd / "uv.lock").write_text("locked")  # install creates one

    rev.undo_last()
    assert not (wd / "uv.lock").exists()  # removed on undo
    assert rev.last_changed_lockfiles == ["uv.lock"]


def test_undo_no_lockfile_change_is_silent(tmp_path):
    """A plain file edit with no lockfile involved reports nothing to warn about."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    (wd / "a.txt").write_text("v1")
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))
    rev.before_action("write", "a.txt", reversible=True)
    (wd / "a.txt").write_text("v2")
    rev.undo_last()
    assert rev.last_changed_lockfiles == []


def test_no_snapshot_action_logs_but_captures_nothing(tmp_path):
    """OPENDOT_NO_SNAPSHOT path: the action is logged (audit trail) but no
    snapshot is taken, so nothing sensitive is copied into the store."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    (wd / "secret.txt").write_text("password123")
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))

    entry_id = rev.before_action("shell", "shred secret.txt", snapshot=False)
    assert entry_id  # a real, sortable id
    entries = rev.history()
    assert len(entries) == 1
    assert entries[0].snapshot_before == ""  # nothing to restore from
    assert entries[0].reversible is False
    # no snapshot manifest was written for this project
    assert S.list_snapshots(rev.project_id) == []
    # and the secret content is NOT sitting in the object store
    objs = S.store_root() / "objects"
    assert not objs.exists() or not any(objs.iterdir())


def test_undo_noops_on_no_snapshot_action(tmp_path):
    """Undoing a no-snapshot action must not crash — there's nothing to restore."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))
    rev.before_action("shell", "shred x", snapshot=False)
    assert rev.undo_last() is None  # no-op, not an exception


def test_no_snapshot_ids_stay_monotonic_with_real_snapshots(tmp_path):
    """A no-snapshot entry followed by a real snapshot keeps ids sortable/unique."""
    from opendot.reversibility.engine import Reversibility
    from opendot.reversibility.rules import load_rules

    wd = _workspace(tmp_path)
    (wd / "a.txt").write_text("v1")
    rev = Reversibility(workdir=str(wd), rules=load_rules(str(wd)))

    id1 = rev.before_action("write", "a.txt", reversible=True)  # real snap
    id2 = rev.before_action("shell", "shred y", snapshot=False)  # no snap
    id3 = rev.before_action("write", "a.txt", reversible=True)  # real snap
    ids = [id1, id2, id3]
    assert ids == sorted(ids)  # strictly increasing
    assert len(set(ids)) == 3  # all unique
