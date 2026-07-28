<!-- Absolute raw-GitHub URLs so the logo/screenshot also render on PyPI, which
     can't resolve relative repo paths. When a dark-mode logo exists, add a
     <source ... media="(prefers-color-scheme: dark)"> line to the <picture>. -->
<p align="center">
  <a href="https://pypi.org/project/opendot/">
    <img src="https://raw.githubusercontent.com/vedaant00/opendot/main/assets/logo-full.png" alt="opendot" width="360" />
  </a>
  <br />
  An interactive terminal AI agent you can fully undo.
</p>

<p align="center">
  <a href="https://github.com/vedaant00/opendot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/vedaant00/opendot/ci.yml?style=flat-square&branch=main&label=ci" /></a>
  <a href="https://pypi.org/project/opendot/"><img alt="PyPI" src="https://img.shields.io/pypi/v/opendot?style=flat-square" /></a>
  <a href="https://pypi.org/project/opendot/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/opendot?style=flat-square" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" /></a>
</p>

<!-- Demo: replace with a real terminal recording/screenshot of a task + undo.
<p align="center">
  <img src="https://raw.githubusercontent.com/vedaant00/opendot/main/assets/demo.gif" alt="opendot demo" width="720" />
</p>
-->

---

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
opendot --model claude-opus-4-5      # launch with a specific model (see below)

opendot log                          # audit: what has the agent done here?
opendot undo                         # revert the last action
opendot undo 000004                  # restore the workspace to before action #4
```

Inside the chat, slash-commands: `/model` (searchable model picker),
`/provider` (connect a provider + paste an API key), `/log`, `/undo`, `/clear`,
`/compact`, `/help`.

## Any model

Any model works — cloud, local, or Hugging Face. You need an API key for the
provider you want to use (opendot is BYO-key; it doesn't host models). Pick a
model and paste a key right inside the chat with `/model` and `/provider`, or
set the key in your environment and pass `--model`:

| Provider | Env var | Example `--model` | Get a key |
|----------|---------|-------------------|-----------|
| OpenAI | `OPENAI_API_KEY` | `gpt-5.1` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-opus-4-5` | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| Google | `GEMINI_API_KEY` | `gemini/gemini-3-pro` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek/deepseek-chat` | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |
| Groq | `GROQ_API_KEY` | `groq/llama-3.3-70b-versatile` | [console.groq.com/keys](https://console.groq.com/keys) |
| Hugging Face | `HF_TOKEN` | `huggingface/together/deepseek-ai/DeepSeek-R1` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| Ollama (local, no key) | — | `ollama/qwen3` | run [ollama.com](https://ollama.com) locally |

Reasoning models stream their thinking live.

**Which model runs.** The default is `gpt-5.1`. If its key (`OPENAI_API_KEY`)
isn't set but another provider's key is, opendot automatically switches to that
provider on launch — e.g. with only `DEEPSEEK_API_KEY` set, a bare `opendot`
uses `deepseek/deepseek-chat`. If **no** provider key is found, opendot starts
fine but the first message shows a hint to set a key or run `/provider` (rather
than a raw provider error). `ollama/*` models need no key — just a local Ollama.

## Connect MCP servers

opendot is an [MCP](https://modelcontextprotocol.io) client: connect any MCP
server and its tools become available to the agent alongside the built-in ones.
Manage them from inside the chat with **`/mcp`** (a dropdown of your servers and
their status, with "➕ Add a server"), or from the command line:

```bash
# a stdio server — put its launch command after `--`
opendot mcp add <name> --env KEY=VALUE -- <command> [args...]

# a remote server (http/sse)
opendot mcp add <name> --url <https url>

# a remote server that needs auth — pass an HTTP header
opendot mcp add supabase \
  --url "https://mcp.supabase.com/mcp?project_ref=<id>&read_only=true" \
  --header "Authorization=Bearer <your-supabase-access-token>"

opendot mcp list           # show configured servers
opendot mcp remove <name>  # remove one
```

Servers are stored in `~/.opendot/mcp.json` and connect automatically on the
next launch; connected servers appear in the sidebar. For authenticated remote
servers, opendot supports the header/token method (e.g. Supabase's access
token) — the interactive browser-OAuth flow is not implemented yet.

Because opendot can't know what an external tool does, **every MCP tool call is
treated as irreversible** — it's confirmed before running and marked ✗ in the
ledger. Your built-in file/shell actions stay snapshotted and undoable as usual.

## Connect apps with Composio

Beyond MCP, opendot can connect to [Composio](https://composio.dev)'s 3000+ app
tools (Gmail, Slack, GitHub, Notion, Linear, …) using **your own** Composio API
key. Install the extra and use `/composio` in the chat:

```bash
pip install 'opendot[composio]'
```

- The first `/composio` asks for your Composio API key (stored in
  `~/.opendot/composio.json`, owner-readable only).
- After that, `/composio` lists the available apps. Pick one — if it needs
  OAuth, opendot opens your browser to authorize and waits for you to finish;
  direct/API-key connectors activate immediately.
- Enabled apps appear in the sidebar; their tools load on the next launch.

Composio tools reach external services, so — like MCP — **every call is treated
as irreversible**: confirmed first, marked ✗ in the ledger.

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

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup and
the one hard rule (don't break reversibility). Security reports go through
[SECURITY.md](SECURITY.md).

```bash
git clone https://github.com/vedaant00/opendot
cd opendot
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
pytest
```

## Status

Early (alpha). The interactive agent, local tools, and the full reversibility
engine work and are tested. Streaming, slash-commands, and `OPENDOT.md` rules
are in. A richer TUI and more tools are coming.

[MIT licensed.](LICENSE)
