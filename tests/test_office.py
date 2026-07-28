"""Tests for the office (.xlsx / .pptx) tools, incl. undoability of binary edits."""

import pytest

pytest.importorskip("openpyxl")
pytest.importorskip("pptx")

from opendot.tools.local import Toolbox
from opendot.reversibility.engine import Reversibility
from opendot.reversibility.snapshots import IgnoreRules


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDOT_HOME", str(tmp_path / "store"))
    yield


def _tb(tmp_path):
    wd = tmp_path / "ws"
    wd.mkdir()
    rev = Reversibility(workdir=str(wd), rules=IgnoreRules())
    return Toolbox(str(wd), reversibility=rev, confirm=lambda p: True), wd, rev


def _make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "name"; ws["B1"] = "score"
    ws["A2"] = "alice"; ws["B2"] = 100
    wb.save(path)


def _make_pptx(path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tb.text_frame.text = "Old Title"
    prs.save(path)


def test_office_tools_registered(tmp_path):
    tb, _, _ = _tb(tmp_path)
    names = {s["function"]["name"] for s in tb.specs()}
    assert {"read_xlsx", "edit_cell", "read_pptx", "edit_pptx_text"} <= names


def test_read_and_edit_xlsx(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    _make_xlsx(wd / "data.xlsx")

    out = tb.call("read_xlsx", {"path": "data.xlsx"})
    assert "alice" in out and "score" in out

    res = tb.call("edit_cell", {"path": "data.xlsx", "cell": "B2", "value": "120"})
    assert "100" in res and "120" in res

    import openpyxl
    wb = openpyxl.load_workbook(wd / "data.xlsx")
    assert wb.active["B2"].value == 120  # coerced to int


def test_xlsx_edit_is_undoable(tmp_path):
    tb, wd, rev = _tb(tmp_path)
    _make_xlsx(wd / "data.xlsx")
    tb.call("edit_cell", {"path": "data.xlsx", "cell": "B2", "value": "999"})

    import openpyxl
    assert openpyxl.load_workbook(wd / "data.xlsx").active["B2"].value == 999
    rev.undo_last()  # restore the binary file exactly
    assert openpyxl.load_workbook(wd / "data.xlsx").active["B2"].value == 100


def test_read_and_edit_pptx(tmp_path):
    tb, wd, rev = _tb(tmp_path)
    _make_pptx(wd / "deck.pptx")

    out = tb.call("read_pptx", {"path": "deck.pptx"})
    assert "Old Title" in out

    res = tb.call("edit_pptx_text", {"path": "deck.pptx", "find": "Old Title", "replace": "New Title"})
    assert "1 run" in res

    from pptx import Presentation
    prs = Presentation(str(wd / "deck.pptx"))
    texts = [sh.text for sh in prs.slides[0].shapes if sh.has_text_frame]
    assert "New Title" in texts

    rev.undo_last()  # binary pptx restored
    prs2 = Presentation(str(wd / "deck.pptx"))
    texts2 = [sh.text for sh in prs2.slides[0].shapes if sh.has_text_frame]
    assert "Old Title" in texts2


def test_edit_pptx_missing_text_is_error(tmp_path):
    tb, wd, _ = _tb(tmp_path)
    _make_pptx(wd / "deck.pptx")
    res = tb.call("edit_pptx_text", {"path": "deck.pptx", "find": "nope", "replace": "x"})
    assert "not found" in res
