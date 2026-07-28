"""Parse snapshot include/exclude rules out of an OPENDOT.md file.

Users put project guidance in OPENDOT.md (read into the system prompt). They can
*also* control what opendot snapshots by adding an ```opendot``` fenced block:

    ```opendot
    # snapshot these even though they're skipped by default:
    snapshot: node_modules, dist
    # never snapshot these (in addition to the defaults):
    skip: data, *.log, secrets/
    ```

`snapshot:` entries force-include names that the defaults would skip (e.g. venv).
`skip:` entries add to the skip set. Directive names are matched against path
*component names* (same model as IgnoreRules). Missing block / missing file =>
plain defaults. Parsing is forgiving: unknown lines are ignored, never raised.
"""

from __future__ import annotations

import re
from pathlib import Path

from opendot.reversibility.snapshots import IgnoreRules

_BLOCK_RE = re.compile(r"```opendot\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _split_list(value: str) -> list[str]:
    # comma- or whitespace-separated; strip trailing slashes for name matching
    parts = re.split(r"[,\s]+", value.strip())
    return [p.rstrip("/").strip() for p in parts if p.strip()]


def parse_rules_text(text: str) -> IgnoreRules:
    rules = IgnoreRules()
    m = _BLOCK_RE.search(text)
    if not m:
        return rules
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        items = _split_list(value)
        if key in {"snapshot", "include"}:
            rules.force_include.update(items)
        elif key in {"skip", "exclude", "ignore"}:
            rules.extra_skip.update(items)
    return rules


def load_rules(workdir: str | Path) -> IgnoreRules:
    """Load IgnoreRules from OPENDOT.md in workdir, or defaults if absent."""
    p = Path(workdir) / "OPENDOT.md"
    if not p.exists():
        return IgnoreRules()
    try:
        return parse_rules_text(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - never let a bad file break the agent
        return IgnoreRules()
