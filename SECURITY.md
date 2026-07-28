# Security

## Threat model

### Overview

opendot is an AI-powered agent that runs **locally on your machine**. It gives a
model access to real tools: shell execution, file read/write/edit, and — if you
configure them — external MCP servers.

### No sandbox

opendot does **not** sandbox the agent. Two features exist to keep you in
control, but neither is a security boundary:

- **Confirmation prompts** ask before running commands opendot judges
  irreversible or workspace-escaping (network, `sudo`, deletes outside the
  working dir, external MCP calls). This is a UX safety net, not isolation.
- **Reversibility** snapshots the workspace before each action so you can `undo`
  file/shell changes. This lets you *walk back* mistakes made inside the
  workspace — it does **not** prevent an action, and it cannot undo effects that
  leave your machine (a sent request, a dropped remote database, `git push`).

If you need true isolation, run opendot inside a Docker container or VM.

### Out of scope

| Category | Rationale |
|----------|-----------|
| Sandbox escapes | opendot is not a sandbox — the confirm/undo system is a UX layer (see above). |
| Effects outside the workspace | Actions that leave your machine are flagged as irreversible before running, but cannot be undone; running them is your decision. |
| LLM provider data handling | Data sent to your configured model provider is governed by their policies. |
| MCP server behavior | External MCP servers you configure are outside opendot's trust boundary. |
| Malicious config files | You control your own `~/.opendot` config; modifying it is not an attack vector. |

## Reporting a vulnerability

> [!IMPORTANT]
> We do not accept AI-generated security reports. Please only submit findings you
> have personally verified.

To report a security issue, use the repository's **GitHub Security Advisory
"Report a Vulnerability"** tab. We'll acknowledge your report and keep you
informed of progress toward a fix, and may ask for additional detail.

We appreciate responsible disclosure and will make every effort to acknowledge
your contribution.
