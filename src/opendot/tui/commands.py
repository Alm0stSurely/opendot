"""The slash-command registry — single source of truth for the autocomplete
popup (name, one-line description)."""

from __future__ import annotations

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/model", "switch model (searchable picker)"),
    ("/provider", "connect a provider + API key"),
    ("/mcp", "manage MCP servers"),
    ("/composio", "connect apps (Gmail, Slack, …)"),
    ("/log", "show the action ledger ( /log clear to wipe it )"),
    ("/undo", "revert the last action ( /undo <id> )"),
    ("/clear", "reset the conversation"),
    ("/compact", "trim old turns to free context"),
    ("/help", "list commands"),
]
