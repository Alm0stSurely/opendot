"""Events streamed by the agent loop.

The loop is an async generator of these. The CLI renders them; a future SDK
consumer or remote client can consume them just as easily. Keeping this a small,
stable vocabulary is what lets other surfaces (TUI, web, Slack) be thin adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "thinking",    # a streamed chunk of the model's reasoning (reasoning models)
    "text",        # a streamed chunk of the assistant's answer
    "tool_start",  # the agent is about to run a tool
    "tool_end",    # a tool finished (result attached)
    "final",       # the assistant's turn is complete
    "error",       # something went wrong
    # parallel read-only explorers
    "explorer_start",  # a subagent lane started (text=task, lane=index)
    "explorer_step",   # a subagent did something (text=summary, lane=index)
    "explorer_done",   # a subagent finished (text=finding summary, lane=index)
]


@dataclass
class Event:
    type: EventType
    text: str = ""                       # for "text" / "error" / explorer_*
    tool: str = ""                       # for tool_start / tool_end
    args: dict[str, Any] = field(default_factory=dict)  # tool_start
    result: str = ""                     # tool_end
    lane: int = -1                       # explorer lane index (-1 = main agent)
