<div align="center">

<!-- Absolute raw-GitHub URL so the logo also renders on PyPI (which can't
     resolve relative repo paths). File: assets/logo-full.png -->
<img src="https://raw.githubusercontent.com/vedaant00/opendot/main/assets/logo-full.png" alt="opendot" width="360" />

**An interactive terminal AI agent you can fully undo.**

[![CI](https://github.com/vedaant00/opendot/actions/workflows/ci.yml/badge.svg)](https://github.com/vedaant00/opendot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/opendot.svg)](https://pypi.org/project/opendot/)
[![Downloads](https://static.pepy.tech/badge/opendot)](https://pepy.tech/project/opendot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>


opendot works directly on your real files and shell — but unlike other terminal
agents, **every action it takes is snapshotted first**, so you can see exactly
what it did and cleanly walk it back. Files *and* shell commands, not just
in-repo edits. Commands whose effects escape your workspace (network, sudo,
`git push`, deleting outside the working dir) are flagged and confirmed before
they run, with an honest note about what can't be undone.

That's the point of opendot: an agent you can let loose because nothing it does
is a surprise, and (almost) nothing is irreversible.

## Install

```bash
# try it instantly, no install
uvx opendot

# recommended (isolated global CLI)
uv tool install opendot        # or: pipx install opendot

# also works
pip install opendot
```

## Use

```bash
opendot                              # open an interactive chat
opendot -p "summarize this project"  # one-shot, for scripts / CI
opendot --model claude-sonnet-4-5    # any model (see below)

opendot log                          # audit: what has the agent done here?
opendot undo                         # revert the last action
opendot undo 000004                  # restore the workspace to before action #4
```

Inside the chat, slash-commands: `/log`, `/undo`, `/clear`, `/compact`,
`/model`, `/help`.

## Any model

opendot uses [LiteLLM](https://docs.litellm.ai), so any model works — cloud,
local, or Hugging Face. Set the provider's API key in your environment and pass
`--model`:

| Provider | Env var | Example `--model` |
|----------|---------|-------------------|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` |
| Google | `GEMINI_API_KEY` | `gemini/gemini-2.0-flash` |
| Ollama (local) | — | `ollama/qwen2.5` |

Reasoning models stream their thinking live.

## Connect MCP servers

opendot is an [MCP](https://modelcontextprotocol.io) client: connect any MCP
server and its tools become available to the agent alongside the built-in ones.

```bash
# a stdio server — put its launch command after `--`
opendot mcp add <name> --env KEY=VALUE -- <command> [args...]

# a remote server (http/sse)
opendot mcp add <name> --url <https url>

opendot mcp list           # show configured servers
opendot mcp remove <name>  # remove one
```

Servers are stored in `~/.opendot/mcp.json` and connect automatically on the
next launch; connected servers appear in the sidebar.

Because opendot can't know what an external tool does, **every MCP tool call is
treated as irreversible** — it's confirmed before running and marked ✗ in the
ledger. Your built-in file/shell actions stay snapshotted and undoable as usual.

## Project rules — `OPENDOT.md`

Drop an `OPENDOT.md` in your project. Its prose is given to the agent as
context. You can also control what gets snapshotted with an `opendot` block:

````markdown
```opendot
# snapshot these even though they'd normally be skipped:
snapshot: dist
# never snapshot these:
skip: data, *.log
```
````

By default opendot skips `.git`, `node_modules`, virtualenvs, and build caches
when snapshotting — your rules override those in either direction.

## How the reversibility works

- Before every file write or shell command, opendot snapshots the working
  directory into a **content-addressed store** in `~/.opendot` (each unique file
  stored once, so snapshots are cheap).
- Every action is recorded in an **append-only ledger** you can inspect with
  `opendot log`.
- `opendot undo` restores the workspace to a chosen point, exactly.
- A conservative **classifier** decides which shell commands are workspace-
  contained (auto-run, undoable) vs. escaping (confirmed first, marked
  irreversible). When unsure, it asks.

Honest boundary: opendot cannot undo effects that leave your machine (a sent
email, a dropped remote database, a `git push`). It tells you *before* running
those, rather than pretending otherwise.

## Status

Early (alpha). The interactive agent, local tools, and the full reversibility
engine work and are tested. Streaming, slash-commands, and `OPENDOT.md` rules
are in. A richer TUI and more tools are coming.

MIT licensed.
