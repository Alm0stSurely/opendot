"""The slash-command registry — single source of truth for the autocomplete
popup (name, one-line description)."""

from __future__ import annotations

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/model", "switch model (searchable picker)"),
    ("/provider", "connect a provider + API key"),
    ("/mcp", "manage MCP servers"),
    ("/composio", "connect apps (Gmail, Slack, …)"),
    ("/log", "show the action ledger ( /log clear to wipe it )"),
    ("/trace", "per-model-call cost and timing for this session"),
    ("/diff", "preview what /undo <id> would change ( /diff <id> )"),
    ("/undo", "revert the last action ( /undo <id> )"),
    ("/redo", "re-apply the action /undo just reverted"),
    ("/clear", "reset the conversation"),
    ("/save", "save this project's conversation"),
    ("/resume", "reload this project's saved conversation"),
    ("/compact", "trim old turns to free context"),
    ("/help", "list commands"),
]
