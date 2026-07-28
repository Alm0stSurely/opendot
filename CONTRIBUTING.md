# Contributing to opendot

Thanks for your interest! opendot is an interactive terminal AI agent whose
defining feature is **trustworthy reversibility** — you can see and cleanly undo
everything it does. Contributions are welcome; please keep that guarantee intact.

opendot is early. Issues and PRs are genuinely appreciated — you don't need to
ask permission to open one.

## Changes that are easy to merge

- Bug fixes
- New tools (and improvements to existing ones)
- New model providers / fixing provider- or environment-specific quirks
- MCP integration improvements
- Better prompts / agent behavior
- Documentation improvements

For a large new **product feature** (especially anything touching the
reversibility engine or the TUI's core), please open an issue to discuss the
approach first — it saves you building something that needs reworking.

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

## Pull requests

- **Keep them small and focused.** One change per PR.
- **Add tests** for your change. Run `pytest` before opening — CI runs it on
  Python 3.10–3.12.
- **Explain how you verified it** in your own words: what you tested, and how a
  reviewer can reproduce the result.
- **UI changes:** include a screenshot or short recording (before/after).
- **No AI-generated walls of text.** Short, honest descriptions in your own
  words. If you can't explain the change briefly, it's probably too large.
- **Don't add fake affordances** — no UI hints or commands for features that
  don't actually work.

### PR titles

Follow conventional commits:

- `feat:` — new feature or tool
- `fix:` — bug fix
- `docs:` — documentation
- `refactor:` — behavior-preserving refactor
- `test:` — tests
- `chore:` — maintenance / deps

## Style

Match the surrounding code: small, clear functions; comments explain *why*, not
*what*; precise types; avoid needless complexity.
