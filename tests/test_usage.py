"""Usage/cost accounting tests.

Covers the streaming path's two past bugs: cost showing $0 (computed from raw
chunks) and tokens double-counting when a provider reports usage on both the
last content chunk and a final usage-only chunk.
"""

import asyncio
import types

from opendot.agent.config import AgentConfig
from opendot.agent.loop import Agent
from opendot.agent.usage import Usage


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 500
    total_tokens = 1500


class _Delta:
    content = None
    reasoning_content = None
    tool_calls = None


class _Choice:
    def __init__(self):
        self.delta = _Delta()


def _chunk(with_choices, with_usage):
    c = types.SimpleNamespace()
    c.choices = [_Choice()] if with_choices else []
    c.usage = _Usage() if with_usage else None
    return c


class _FakeLiteLLM(types.SimpleNamespace):
    async def acompletion(self, **kw):
        async def gen():
            yield _chunk(True, False)   # content only
            yield _chunk(True, True)    # last content chunk WITH usage
            yield _chunk(False, True)   # final usage-only chunk (duplicate)
        return gen()

    def cost_per_token(self, model, prompt_tokens, completion_tokens):
        return (0.005, 0.0025)


def _bare_agent():
    a = Agent.__new__(Agent)
    a.config = AgentConfig(model="gpt-4o", workdir="/tmp")
    a.usage = Usage()
    a.messages = []
    a.toolbox = types.SimpleNamespace(_confirm=None, specs=lambda: [])
    return a


def test_streaming_usage_counted_once_not_doubled():
    a = _bare_agent()

    async def run():
        async for _ in a._stream_turn(_FakeLiteLLM(), []):
            pass

    asyncio.run(run())
    assert a.usage.total_tokens == 1500          # not 3000
    assert round(a.usage.cost_usd, 4) == 0.0075  # not 0.015


def test_add_response_cost_from_tokens():
    """Cost is derived from token counts + model (works for stream chunks),
    not only from completion_cost."""
    import litellm

    u = Usage()
    resp = types.SimpleNamespace(usage=_Usage())
    u.add_response(resp, litellm, model="gpt-4o")
    assert u.total_tokens == 1500
    assert u.cost_usd > 0  # real pricing for gpt-4o
