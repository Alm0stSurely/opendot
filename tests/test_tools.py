"""Tests for the coding tools (grep, glob, edit) and that edit is undoable."""

import os
from pathlib import Path

import pytest

from opendot.tools.local import Toolbox
from opendot.reversibility.engine import Reversibility
from opendot.reversibility.snapshots import IgnoreRules


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    yield


def _tb(tmp_path):
    wd = tmp_path / "ws"
    (wd / "src").mkdir(parents=True)
    (wd / "src" / "a.py").write_text("x = 1  # TODO fix\ny = 2\n")
    (wd / "src" / "b.py").write_text("z = 3\n")
    (wd / "README.md").write_text("hello\n")
    rev = Reversibility(workdir=str(wd), rules=IgnoreRules())
    return Toolbox(str(wd), reversibility=rev, confirm=lambda p: True), wd, rev


def test_grep_finds_matches(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    out = tb.call("grep", {"pattern": "TODO"})
    assert "a.py:1:" in out
    assert "TODO" in out


def test_grep_no_match(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    assert tb.call("grep", {"pattern": "nonexistent_xyz"}) == "no matches"


def test_glob(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    out = tb.call("glob", {"pattern": "**/*.py"})
    assert "src/a.py" in out and "src/b.py" in out
    assert "README.md" not in out


def test_edit_replaces_and_reports(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    out = tb.call("edit", {"path": "src/b.py", "find": "z = 3", "replace": "z = 99"})
    assert "1 replacement" in out
    assert "-z = 3" in out and "+z = 99" in out  # diff is shown
    assert (wd / "src" / "b.py").read_text() == "z = 99\n"


def test_edit_missing_find_is_error_no_change(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    before = (wd / "src" / "b.py").read_text()
    out = tb.call("edit", {"path": "src/b.py", "find": "not there", "replace": "x"})
    assert "not present" in out
    assert (wd / "src" / "b.py").read_text() == before  # unchanged


def test_edit_is_undoable(tmp_path):
    tb, wd, rev = _tb(tmp_path)
    tb.call("edit", {"path": "src/a.py", "find": "x = 1", "replace": "x = 42"})
    assert "x = 42" in (wd / "src" / "a.py").read_text()

    rev.undo_last()  # walk back the edit
    assert (wd / "src" / "a.py").read_text() == "x = 1  # TODO fix\ny = 2\n"


def test_new_tools_are_in_specs(tmp_path):
    tb, _, _ = _tb(tmp_path)
    names = {s["function"]["name"] for s in tb.specs()}
    assert {"grep", "glob", "edit", "run_shell", "read_file", "write_file", "list_files"} <= names
