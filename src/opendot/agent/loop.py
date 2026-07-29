"""The agent loop — a model-agnostic ReAct loop over LiteLLM.

`Agent.run(user_message)` is an async generator of `Event`s. It keeps a running
conversation (`self.messages`) across turns so the CLI REPL is just repeated
`run(...)` calls. Tool calls are executed locally via the `Toolbox` and fed back
to the model until it produces a final answer (no more tool calls).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from opendot.agent.config import AgentConfig
from opendot.agent.events import Event
from opendot.agent.prompt import DEFAULT_SYSTEM_PROMPT
from opendot.tools.local import Toolbox


_SPAWN_EXPLORERS_SPEC = {
    "type": "function",
    "function": {
        "name": "spawn_explorers",
        "description": (
            "Investigate several INDEPENDENT questions about the project in "
            "parallel. Each task runs as a read-only subagent (grep/glob/read "
            "only — it cannot change anything) and returns findings. Use this to "
            "understand a codebase fast by splitting distinct questions across "
            "lanes. For anything that changes files, do it yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array", "items": {"type": "string"},
                    "description": "2-6 independent, self-contained investigation tasks.",
                }
            },
            "required": ["tasks"],
        },
    },
}


class _StreamUnsupported(Exception):
    """Raised when a provider's stream can't yield structured tool calls."""


@dataclass
class _Assembled:
    """Sentinel yielded at the end of a turn carrying the assembled result."""

    assistant_msg: dict[str, Any]
    tool_calls: list[dict[str, Any]]


def _assistant_msg(content: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
            for c in calls
        ]
    return msg


class Agent:
    def __init__(self, config: AgentConfig | None = None, confirm=None,
                 mcp_manager=None) -> None:
        self.config = config or AgentConfig()
        self.mcp = mcp_manager

        # The reversibility engine snapshots + logs before every mutating action.
        # Snapshot include/exclude rules come from OPENDOT.md (or defaults).
        from opendot.reversibility.engine import Reversibility
        from opendot.reversibility.rules import load_rules

        self.reversibility = Reversibility(
            workdir=self.config.workdir, rules=load_rules(self.config.workdir)
        )
        self.toolbox = Toolbox(
            self.config.workdir,
            reversibility=self.reversibility,
            confirm=confirm,
            mcp_manager=mcp_manager,
        )
        system = self.config.system_prompt or DEFAULT_SYSTEM_PROMPT
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

        from opendot.agent.usage import Usage

        self.usage = Usage()  # running token/cost totals for the session

        # The main agent can fan out read-only explorer subagents; explorers
        # themselves cannot (no recursive spawning). Read-only toolboxes = explorer.
        self.explorers_enabled = not getattr(self.toolbox, "read_only", False)

    def reset(self) -> None:
        """Clear the conversation (the /clear context-reset barrier), keeping system."""
        self.messages = self.messages[:1]

    def compact(self, keep_recent: int = 6) -> int:
        """Trim old turns to fight context bloat, keeping the system prompt and
        the most recent messages. Returns how many messages were dropped.

        v1 is a simple truncation (honest and predictable). A summarizing
        compaction can replace this later without changing the interface.
        """
        if len(self.messages) <= keep_recent + 1:
            return 0
        system, tail = self.messages[:1], self.messages[-keep_recent:]
        dropped = len(self.messages) - len(system) - len(tail)
        self.messages = system + tail
        return dropped

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        """Run one user turn to completion, yielding events."""
        import litellm

        # Keep litellm/provider chatter out of the user's terminal.
        litellm.suppress_debug_info = True
        litellm.set_verbose = False

        self.messages.append({"role": "user", "content": user_message})
        tools = self.toolbox.specs()
        if self.explorers_enabled:
            tools = tools + [_SPAWN_EXPLORERS_SPEC]

        for _ in range(self.config.max_steps):
            # Stream the turn: reasoning ("thinking") and answer ("text") arrive
            # live, and tool-call deltas are reassembled. Streaming is what makes
            # the UX feel alive. If streaming fails for a provider, fall back to
            # a single non-streaming call (some local models emit tool calls as
            # text in streaming mode).
            try:
                gen = None
                async for ev in self._stream_turn(litellm, tools):
                    if isinstance(ev, _Assembled):
                        gen = ev
                    else:
                        yield ev
            except _StreamUnsupported:
                gen = None
                async for ev in self._nonstream_turn(litellm, tools):
                    if isinstance(ev, _Assembled):
                        gen = ev
                    else:
                        yield ev
            except Exception as exc:  # noqa: BLE001
                yield Event("error", text=f"model call failed: {exc}")
                return

            if gen is None:  # an error event was already yielded
                return

            self.messages.append(gen.assistant_msg)

            if not gen.tool_calls:
                yield Event("final")
                return

            for i, c in enumerate(gen.tool_calls):
                name, call_id, raw_args = c["name"], c["id"], c["args"]
                try:
                    args = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    args = {}

                # spawn_explorers is handled by the runtime (parallel read-only
                # subagents), not the toolbox — it streams lane-tagged events.
                if name == "spawn_explorers" and self.explorers_enabled:
                    from opendot.agent.explorers import run_explorers

                    result = "(no findings)"
                    async for ev in run_explorers(
                        args.get("tasks", []), model=self.config.model,
                        workdir=self.config.workdir,
                    ):
                        if ev.type == "tool_end" and ev.tool == "spawn_explorers":
                            result = ev.result  # merged findings
                        else:
                            yield ev
                    self.messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": result}
                    )
                    continue

                yield Event("tool_start", tool=name, args=args)
                # Run the (synchronous) tool off the event loop. This is what lets
                # a confirm-callback safely block for a UI prompt (e.g. the TUI
                # modal) without freezing the loop.
                import asyncio
                result = await asyncio.to_thread(self.toolbox.call, name, args)
                yield Event("tool_end", tool=name, result=result)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )

        yield Event("error", text=f"stopped: hit max_steps ({self.config.max_steps})")

    async def _stream_turn(self, litellm, tools):
        """Stream one model turn. Yields Events, then a final _Assembled sentinel.

        Raises _StreamUnsupported if the stream looks like a provider that emits
        tool calls as plain text (so the caller can fall back to non-streaming).
        """
        stream = await litellm.acompletion(
            model=self.config.model, messages=self.messages, tools=tools,
            temperature=self.config.temperature, stream=True,
            stream_options={"include_usage": True},
            api_base=self.config.api_base,  # None => provider default
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        # Guard: with include_usage some providers report usage on both the last
        # content chunk and a final usage-only chunk — count it once per turn.
        usage_counted = False

        async for chunk in stream:
            # Usage may arrive on its own final chunk (no choices) or attached to
            # a content chunk — capture it either way, but only once.
            if not usage_counted and getattr(chunk, "usage", None):
                self.usage.add_response(chunk, litellm, model=self.config.model)
                usage_counted = True
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # reasoning models expose reasoning separately
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield Event("thinking", text=reasoning)
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                yield Event("text", text=delta.content)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments

        calls = [
            {"id": c["id"] or f"call_{i}", "name": c["name"], "args": c["args"]}
            for i, c in sorted(tool_calls.items()) if c["name"]
        ]
        yield _Assembled(_assistant_msg("".join(content_parts), calls), calls)

    async def _nonstream_turn(self, litellm, tools):
        """Non-streaming fallback (reliable tool calls; no live tokens)."""
        resp = await litellm.acompletion(
            model=self.config.model, messages=self.messages, tools=tools,
            temperature=self.config.temperature, stream=False,
            api_base=self.config.api_base,  # None => provider default
        )
        self.usage.add_response(resp, litellm, model=self.config.model)
        msg = resp.choices[0].message
        raw = getattr(msg, "tool_calls", None) or []
        calls = [
            {"id": tc.id or f"call_{i}", "name": tc.function.name,
             "args": tc.function.arguments or "{}"}
            for i, tc in enumerate(raw)
        ]
        if msg.content:
            yield Event("text", text=msg.content)
        yield _Assembled(_assistant_msg(msg.content or "", calls), calls)
