"""Office-document tools: read/edit .xlsx and .pptx structurally.

These let the agent work with spreadsheets and presentations — file types that
`read_file`/`edit` can't touch (they're binary zips of XML). Each edit snapshots
the file first (via the same reversibility engine), so even a binary edit is
fully undoable: if the agent mangles a workbook, `undo` restores the exact bytes.
That "let the agent touch your spreadsheet, and cleanly revert if it botches it"
guarantee is unique to opendot.

Requires the ``office`` extra: ``pip install 'opendot[office]'``. If the libs
aren't installed, the tools aren't registered (see build_office_tools).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def office_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        import pptx  # noqa: F401
        return True
    except ImportError:
        return False


def build_office_tools(box) -> list:
    """Return the office Tools bound to a Toolbox (`box`). Empty if libs absent."""
    if not office_available():
        return []

    from opendot.tools.local import Tool, _truncate

    def _snapshot(p: Path) -> None:
        if box.rev is not None:
            box.rev.before_action("write", str(p), reversible=True)

    # ---- xlsx ----
    def read_xlsx(path: str, sheet: str | None = None, max_rows: int = 100) -> str:
        import openpyxl
        p = box._resolve(path)
        if not p.exists():
            return f"error: file not found: {p}"
        wb = openpyxl.load_workbook(p, data_only=True)
        names = wb.sheetnames
        ws = wb[sheet] if sheet else wb[names[0]]
        lines = [f"workbook {box._rel(p)}  sheets: {', '.join(names)}",
                 f"sheet '{ws.title}'  ({ws.max_row} rows x {ws.max_column} cols)"]
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r > max_rows:
                lines.append(f"... ({ws.max_row - max_rows} more rows)")
                break
            cells = "\t".join("" if v is None else str(v) for v in row)
            lines.append(f"{r}: {cells}")
        return _truncate("\n".join(lines))

    def edit_cell(path: str, cell: str, value: str, sheet: str | None = None) -> str:
        import openpyxl
        p = box._resolve(path)
        if not p.exists():
            return f"error: file not found: {p}"
        wb = openpyxl.load_workbook(p)
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        old = ws[cell].value
        _snapshot(p)
        # numbers stay numbers; formulas (leading '=') pass through
        v: Any = value
        if not value.startswith("="):
            try:
                v = int(value)
            except ValueError:
                try:
                    v = float(value)
                except ValueError:
                    v = value
        ws[cell] = v
        wb.save(p)
        return f"{box._rel(p)} [{ws.title}] {cell}: {old!r} → {v!r}"

    # ---- pptx ----
    def read_pptx(path: str) -> str:
        from pptx import Presentation
        p = box._resolve(path)
        if not p.exists():
            return f"error: file not found: {p}"
        prs = Presentation(str(p))
        lines = [f"presentation {box._rel(p)}  ({len(prs.slides)} slides)"]
        for i, slide in enumerate(prs.slides, 1):
            texts = [sh.text.strip() for sh in slide.shapes
                     if sh.has_text_frame and sh.text.strip()]
            lines.append(f"— slide {i} —")
            lines.extend(f"   {t}" for t in texts)
        return _truncate("\n".join(lines))

    def edit_pptx_text(path: str, find: str, replace: str) -> str:
        from pptx import Presentation
        p = box._resolve(path)
        if not p.exists():
            return f"error: file not found: {p}"
        prs = Presentation(str(p))
        hits = 0
        for slide in prs.slides:
            for sh in slide.shapes:
                if not sh.has_text_frame:
                    continue
                for para in sh.text_frame.paragraphs:
                    for run in para.runs:
                        if find in run.text:
                            run.text = run.text.replace(find, replace)
                            hits += 1
        if hits == 0:
            return f"error: text {find!r} not found in {box._rel(p)} (no change)"
        _snapshot(p)
        prs.save(str(p))
        return f"{box._rel(p)}: replaced {find!r} → {replace!r} in {hits} run(s)"

    return [
        Tool("read_xlsx", "Read an .xlsx spreadsheet: lists sheets and prints a sheet's cells as rows.",
             {"type": "object", "properties": {"path": {"type": "string"}, "sheet": {"type": "string"}, "max_rows": {"type": "integer"}}, "required": ["path"]},
             read_xlsx),
        Tool("edit_cell", "Set one cell in an .xlsx (e.g. cell 'B4'). Numbers/formulas handled. Undoable.",
             {"type": "object", "properties": {"path": {"type": "string"}, "cell": {"type": "string", "description": "A1-style ref, e.g. 'B4'."}, "value": {"type": "string"}, "sheet": {"type": "string"}}, "required": ["path", "cell", "value"]},
             edit_cell),
        Tool("read_pptx", "Read a .pptx presentation: outputs each slide's text outline.",
             {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
             read_pptx),
        Tool("edit_pptx_text", "Find-and-replace text across all slides of a .pptx. Undoable.",
             {"type": "object", "properties": {"path": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}}, "required": ["path", "find", "replace"]},
             edit_pptx_text),
    ]
