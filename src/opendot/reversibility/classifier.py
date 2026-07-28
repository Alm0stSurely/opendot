"""Irreversibility classifier — the honesty layer.

Before opendot runs a shell command, this decides whether the command's effects
stay *inside* the workspace (so a snapshot can undo it) or *escape* it (network,
sudo, package installs, destructive DB, deletes/writes outside the working dir).
Escaping commands are marked not-reversible so the caller confirms first and the
ledger records them honestly.

Design stance: CONSERVATIVE / fail-safe. When in doubt, treat a command as
irreversible. A few extra confirmations are fine; a silent un-undoable surprise
is not. This is a heuristic on shell text, not a guarantee — the reason string
explains the call so the user can judge.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# Commands whose whole job is contained, read-only, or trivially reversible.
# Only these auto-run without confirmation.
_SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "pwd", "echo", "grep", "rg", "find", "wc",
    "diff", "tree", "stat", "file", "which", "env", "date", "whoami",
    "python", "python3", "node", "pytest", "go", "cargo",  # running code in-workspace
    "touch", "mkdir", "cp", "mv",  # in-workspace fs ops (snapshot covers them)
    "sed", "awk", "sort", "uniq", "cut", "tr",
}

# Signals that a command escapes the workspace or is hard/impossible to undo.
_NETWORK = {"curl", "wget", "ssh", "scp", "rsync", "ftp", "nc", "telnet"}
_VCS_REMOTE = ("git push", "git pull", "git fetch", "git clone")
_PKG_INSTALL = ("pip install", "pip3 install", "npm install", "npm i ",
                "yarn add", "apt install", "apt-get install", "brew install",
                "uv pip install", "uv add", "cargo install", "gem install")
_PRIVILEGE = {"sudo", "doas", "su"}
_DESTRUCTIVE_DB = re.compile(r"\b(drop|truncate|delete)\s+(table|database|from)\b", re.I)


@dataclass
class Verdict:
    reversible: bool
    reason: str = ""


def _first_word(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.strip().split()
    return parts[0] if parts else ""


def _mentions_outside_path(command: str, workdir: str) -> bool:
    """Heuristic: does the command reference an absolute path or parent-escape
    that leaves the workspace?"""
    wd = str(Path(workdir).resolve())
    # any ../ that could climb out, or absolute paths not under workdir
    if ".." in command:
        return True
    for tok in re.findall(r"(?:^|\s)(/[^\s'\"]+)", command):
        if not str(Path(tok).resolve()).startswith(wd):
            return True
    # writing to $HOME / ~ is outside the workspace
    if re.search(r"(^|\s)~(/|\s|$)|\$HOME", command):
        return True
    return False


def classify(command: str, workdir: str) -> Verdict:
    """Return a Verdict. reversible=True => safe to snapshot+run silently."""
    cmd = command.strip()
    if not cmd:
        return Verdict(True)

    low = cmd.lower()
    head = _first_word(cmd)

    # Hard irreversible / escaping signals (checked first; fail safe).
    if head in _PRIVILEGE or any(p == head for p in _PRIVILEGE):
        return Verdict(False, "runs with elevated privileges (sudo)")
    if head in _NETWORK:
        return Verdict(False, f"network access ({head})")
    if any(low.startswith(v) or f" {v}" in low for v in _VCS_REMOTE):
        return Verdict(False, "interacts with a remote (git push/pull/clone)")
    if any(p in low for p in _PKG_INSTALL):
        return Verdict(False, "installs packages (changes system/env state)")
    if _DESTRUCTIVE_DB.search(cmd):
        return Verdict(False, "destructive database operation")
    if "rm " in low or head == "rm":
        # rm inside the workspace is covered by the snapshot; rm outside isn't.
        if _mentions_outside_path(cmd, workdir):
            return Verdict(False, "deletes files outside the workspace")
        # in-workspace rm is reversible via snapshot
        return Verdict(True)
    if _mentions_outside_path(cmd, workdir):
        return Verdict(False, "touches paths outside the working directory")

    # Known-safe, workspace-contained commands auto-run.
    if head in _SAFE_COMMANDS:
        return Verdict(True)

    # Unknown command: fail safe — confirm, since we can't vouch for its effects.
    return Verdict(False, f"unrecognized command '{head}' — effects unknown")
