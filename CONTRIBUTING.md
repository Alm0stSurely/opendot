# Contributing to opendot

Thanks for your interest! opendot is an interactive terminal AI agent whose
defining feature is **trustworthy reversibility** — you can see and cleanly undo
everything it does. Contributions are welcome; please keep that guarantee intact.

## Setup

```bash
git clone https://github.com/vedaant00/opendot
cd opendot
uv venv && uv pip install -e ".[office,dev]"   # or: python -m venv .venv && pip install -e ".[office,dev]"
pytest                                          # should be all green
```

## Project layout

```
src/opendot/
  cli.py           entry point (subcommands, one-shot, REPL)
  tui.py           the full-screen Textual TUI
  agent/           the model-agnostic ReAct loop (LiteLLM), events, prompt, usage
  tools/           local file/shell tools + office (.xlsx/.pptx)
  reversibility/   THE MOAT: content-addressed snapshots, ledger, undo, classifier
  mcp/             MCP client (connect external MCP servers)
```

## The one rule: don't break reversibility

`reversibility/` is the reason opendot exists. If you touch it, or add a tool
that mutates the filesystem:

- Every mutating action **must** snapshot before it runs (so `undo` restores
  exact prior bytes).
- Anything whose effect can escape the workspace (network, `sudo`, deletes
  outside the working dir, external/MCP calls) **must** be routed through the
  confirmation gate and recorded as irreversible in the ledger.
- The snapshot round-trip must be exact — see `tests/test_snapshots.py`. Add
  tests for any change here; a lying `undo` defeats the whole project.

## Tests

Please add tests for changes. Run `pytest` before opening a PR — CI runs it on
Python 3.10–3.12.

## Style

Match the surrounding code: small, clear functions; comments explain *why*, not
*what*. Keep the tool/UI honest — don't add UI affordances or hints for features
that don't actually work.

## Opening a PR

Describe the change and why. If it's non-trivial, an issue first to align on the
approach is appreciated.
