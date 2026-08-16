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
            yield _chunk(True, False)  # content only
            yield _chunk(True, True)  # last content chunk WITH usage
            yield _chunk(False, True)  # final usage-only chunk (duplicate)

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
    assert a.usage.total_tokens == 1500  # not 3000
    assert round(a.usage.cost_usd, 4) == 0.0075  # not 0.015


def test_add_response_cost_from_tokens():
    """Cost is derived from token counts + model (works for stream chunks), via
    cost_per_token — not the completion_cost helper. Stubbed so it doesn't depend
    on LiteLLM's live pricing data."""
    calls = {"completion_cost": 0}

    class _StubLiteLLM(types.SimpleNamespace):
        def cost_per_token(self, model, prompt_tokens, completion_tokens):
            return (0.01, 0.02)

        def completion_cost(self, **kw):  # must NOT be used when tokens are present
            calls["completion_cost"] += 1
            return 999.0

    u = Usage()
    resp = types.SimpleNamespace(usage=_Usage())
    u.add_response(resp, _StubLiteLLM(), model="gpt-4o")
    assert u.total_tokens == 1500
    assert round(u.cost_usd, 4) == 0.03  # 0.01 + 0.02 from cost_per_token
    assert calls["completion_cost"] == 0  # token-based path was used


def test_add_response_records_per_call_trace():
    """Each add_response appends a CallRecord (model, tokens, cost, latency), and
    the per-call costs/tokens sum to the running totals."""

    class _StubLiteLLM(types.SimpleNamespace):
        def cost_per_token(self, model, prompt_tokens, completion_tokens):
            return (0.01, 0.02)

    u = Usage()
    u.add_response(
        types.SimpleNamespace(usage=_Usage()), _StubLiteLLM(), model="gpt-4o", latency_s=1.5
    )
    u.add_response(
        types.SimpleNamespace(usage=_Usage()), _StubLiteLLM(), model="gpt-4o", latency_s=2.0
    )

    assert len(u.calls) == 2
    first = u.calls[0]
    assert first.model == "gpt-4o"
    assert first.prompt_tokens == 1000 and first.completion_tokens == 500
    assert round(first.cost_usd, 4) == 0.03
    assert first.latency_s == 1.5
    # Per-call costs/tokens sum to the running totals.
    assert round(sum(r.cost_usd for r in u.calls), 4) == round(u.cost_usd, 4)
    assert sum(r.prompt_tokens + r.completion_tokens for r in u.calls) == u.total_tokens


def test_trace_lines_render():
    u = Usage()
    assert u.trace_lines() == ["no model calls yet this session"]
    u.add_response(
        types.SimpleNamespace(usage=_Usage()), types.SimpleNamespace(), model="m", latency_s=0.5
    )
    lines = u.trace_lines()
    assert any("m" in ln and "0.50s" in ln for ln in lines)
    assert any("total:" in ln for ln in lines)
