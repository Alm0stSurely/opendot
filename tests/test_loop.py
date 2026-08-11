"""Agent streaming-loop tests.

Covers the streaming path's choice-less chunk bug: some providers emit a final
usage-only chunk that has no `choices` attribute at all. Direct attribute access
would raise `AttributeError` and be swallowed by the broad `run()` exception
handler as a "model call failed" failure.
"""

import asyncio
import types

from opendot.agent.config import AgentConfig
from opendot.agent.events import Event
from opendot.agent.loop import Agent, _Assembled


class _Usage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _Delta:
    content = None
    reasoning_content = None
    tool_calls = None


class _Choice:
    def __init__(self, content: str | None = None):
        self.delta = types.SimpleNamespace()
        self.delta.content = content
        self.delta.reasoning_content = None
        self.delta.tool_calls = None


def _chunk_no_choices(with_usage: bool = False):
    """A chunk that lacks the `choices` attribute entirely (e.g. usage-only)."""
    c = types.SimpleNamespace()
    c.usage = _Usage() if with_usage else None
    return c


def _chunk_with_choices(content: str | None = None, with_usage: bool = False):
    c = types.SimpleNamespace()
    c.choices = [_Choice(content)]
    c.usage = _Usage() if with_usage else None
    return c


def _bare_agent():
    a = Agent.__new__(Agent)
    a.config = AgentConfig(model="gpt-4o", workdir="/tmp")
    a.usage = types.SimpleNamespace(add_response=lambda *a, **k: None, total_tokens=0, cost_usd=0.0)
    a.messages = []
    a.toolbox = types.SimpleNamespace(_confirm=None, specs=lambda: [])
    return a


def test_stream_turn_skips_choiceless_chunk():
    """A chunk with no `choices` attribute is skipped; the turn completes."""

    class _FakeLiteLLM(types.SimpleNamespace):
        async def acompletion(self, **kw):
            async def gen():
                yield _chunk_no_choices(with_usage=True)  # final usage-only chunk
                yield _chunk_with_choices("hello")  # actual content chunk

            return gen()

    a = _bare_agent()

    async def run():
        events = []
        async for ev in a._stream_turn(_FakeLiteLLM(), []):
            events.append(ev)
        return events

    events = asyncio.run(run())
    assert any(isinstance(ev, _Assembled) for ev in events)
    text_events = [ev for ev in events if isinstance(ev, Event) and ev.type == "text"]
    assert len(text_events) == 1
    assert text_events[0].text == "hello"


def test_stream_turn_choiceless_chunk_not_fatal_in_run():
    """The public `run()` path surfaces the final text, not a model error."""

    class _FakeLiteLLM(types.SimpleNamespace):
        async def acompletion(self, **kw):
            async def gen():
                yield _chunk_no_choices(with_usage=True)
                yield _chunk_with_choices("ok")

            return gen()

    a = Agent(config=AgentConfig(model="gpt-4o", workdir="/tmp", max_steps=1))
    # Replace the litellm module in the run with our stub
    a.messages = [{"role": "system", "content": "s"}]

    async def run():
        events = []
        async for ev in a._stream_turn(_FakeLiteLLM(), []):
            events.append(ev)
        return events

    events = asyncio.run(run())
    assert not any(isinstance(ev, Event) and ev.type == "error" for ev in events)
