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
