"""Benchmark: diff_snapshot vs restore_snapshot throughput.

Measures how quickly the new dry-run diff can inspect a workspace compared to
the actual restore operation it previews. Run from the repo root with:

    .venv/bin/python benchmark_diff.py
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from opendot.reversibility import snapshots as S


def _setup_workspace(tmp: Path, n_files: int = 500) -> Path:
    wd = tmp / "ws"
    wd.mkdir()
    for i in range(n_files):
        (wd / f"file_{i:04d}.txt").write_text(f"content {i}\n")
    return wd


def _mutate(wd: Path, n_files: int = 500) -> None:
    # Modify half, add a few, remove a few so the diff has work to do.
    for i in range(0, n_files, 2):
        (wd / f"file_{i:04d}.txt").write_text(f"modified {i}\n")
    for i in range(10):
        (wd / f"new_{i:04d}.txt").write_text(f"new {i}\n")
    for i in range(5):
        (wd / f"file_{i:04d}.txt").unlink()


def run() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wd = _setup_workspace(tmp)
        snap = S.take_snapshot(wd)
        _mutate(wd)

        t0 = time.perf_counter()
        delta = S.diff_snapshot(snap)
        diff_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        S.restore_snapshot(snap)
        restore_ms = (time.perf_counter() - t0) * 1000

        modified = len(delta["modified"])
        added = len(delta["added"])
        removed = len(delta["removed"])

        print(f"files: 500  modified: {modified}  added: {added}  removed: {removed}")
        print(f"diff:    {diff_ms:8.2f} ms")
        print(f"restore: {restore_ms:8.2f} ms")
        print(f"ratio:   {restore_ms / diff_ms:.2f}x")


if __name__ == "__main__":
    run()
