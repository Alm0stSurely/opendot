"""Agent streaming-loop tests.

Covers the streaming path's choice-less chunk bug: some providers emit a final
usage-only chunk that has no `choices` attribute at all. Direct attribute access
would raise `AttributeError` and be swallowed by the broad `run()` exception
handler as a "model call failed" failure.

Also covers retrying transient provider errors (rate-limit/5xx/timeout) with
backoff instead of aborting the whole turn on the first blip.
"""

import asyncio
import types

import litellm as real_litellm

from opendot.agent.config import AgentConfig
from opendot.agent.events import Event
from opendot.agent.loop import Agent, _Assembled


class _Usage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


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


def _bare_agent(max_retries: int = 3):
    a = Agent.__new__(Agent)
    a.config = AgentConfig(model="gpt-4o", workdir="/tmp", max_retries=max_retries)
    a.usage = types.SimpleNamespace(add_response=lambda *a, **k: None, total_tokens=0, cost_usd=0.0)
    a.messages = [{"role": "system", "content": "sys"}]
    a.toolbox = types.SimpleNamespace(_confirm=None, specs=lambda: [], call=lambda *a, **k: "")
    a.explorers_enabled = False
    return a


class _FlakyLiteLLM(types.SimpleNamespace):
    """Fake litellm module: `acompletion` raises for the first `fail_times`
    calls, then succeeds. Reuses the real exception classes so isinstance
    checks in the retry helper behave exactly as with the real litellm.
    """

    RateLimitError = real_litellm.RateLimitError
    APIConnectionError = real_litellm.APIConnectionError
    Timeout = real_litellm.Timeout
    InternalServerError = real_litellm.InternalServerError
    ServiceUnavailableError = real_litellm.ServiceUnavailableError
    AuthenticationError = real_litellm.AuthenticationError
    BadRequestError = real_litellm.BadRequestError

    def __init__(self, fail_times, make_exc, **kw):
        super().__init__(**kw)
        self.fail_times = fail_times
        self.make_exc = make_exc
        self.calls = 0

    async def acompletion(self, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.make_exc()

        async def gen():
            yield _chunk_with_choices("done", with_usage=True)

        return gen()


def _rate_limit_error():
    return real_litellm.RateLimitError(
        message="rate limited", llm_provider="openai", model="gpt-4o"
    )


def _auth_error():
    return real_litellm.AuthenticationError(
        message="bad key", llm_provider="openai", model="gpt-4o"
    )


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


def test_stream_turn_choiceless_chunk_not_fatal():
    """A choice-less chunk yields no error event from the streaming path."""

    class _FakeLiteLLM(types.SimpleNamespace):
        async def acompletion(self, **kw):
            async def gen():
                yield _chunk_no_choices(with_usage=True)
                yield _chunk_with_choices("ok")

            return gen()

    a = _bare_agent()

    async def run():
        events = []
        async for ev in a._stream_turn(_FakeLiteLLM(), []):
            events.append(ev)
        return events

    events = asyncio.run(run())
    assert not any(isinstance(ev, Event) and ev.type == "error" for ev in events)


def _run_agent(a, litellm, monkeypatch, user_message="hi"):
    import sys

    from opendot.agent import loop as loop_module

    monkeypatch.setitem(sys.modules, "litellm", litellm)
    # Don't actually wait through backoff sleeps in tests. `asyncio` is a
    # single shared module object, so capture the real sleep before patching
    # it — otherwise the replacement would call itself recursively.
    real_sleep = loop_module.asyncio.sleep
    monkeypatch.setattr(loop_module.asyncio, "sleep", lambda *a, **k: real_sleep(0))

    async def go():
        events = []
        async for ev in a.run(user_message):
            events.append(ev)
        return events

    return asyncio.run(go())


def test_run_survives_transient_failures_then_succeeds(monkeypatch):
    """Two rate-limit blips followed by a success: no error event, turn completes."""
    a = _bare_agent(max_retries=3)
    fake = _FlakyLiteLLM(fail_times=2, make_exc=_rate_limit_error)

    events = _run_agent(a, fake, monkeypatch)

    assert fake.calls == 3
    assert not any(ev.type == "error" for ev in events)
    assert any(ev.type == "final" for ev in events)
    text_events = [ev for ev in events if ev.type == "text"]
    assert text_events and text_events[-1].text == "done"


def test_run_does_not_retry_fatal_errors(monkeypatch):
    """An authentication error fails immediately, with no retry attempts."""
    a = _bare_agent(max_retries=3)
    fake = _FlakyLiteLLM(fail_times=99, make_exc=_auth_error)

    events = _run_agent(a, fake, monkeypatch)

    assert fake.calls == 1
    assert len(events) == 1
    assert events[0].type == "error"
    assert "bad key" in events[0].text


def test_run_exhausts_retries_then_yields_terminal_error(monkeypatch):
    """More transient failures than max_retries allows: the turn ultimately fails."""
    a = _bare_agent(max_retries=2)
    fake = _FlakyLiteLLM(fail_times=99, make_exc=_rate_limit_error)

    events = _run_agent(a, fake, monkeypatch)

    # 1 initial attempt + 2 retries = 3 calls total.
    assert fake.calls == 3
    assert len(events) == 1
    assert events[0].type == "error"
    assert "rate limited" in events[0].text


def test_run_zero_max_retries_fails_on_first_transient_error(monkeypatch):
    """max_retries=0 means no retrying at all — the first blip is terminal."""
    a = _bare_agent(max_retries=0)
    fake = _FlakyLiteLLM(fail_times=99, make_exc=_rate_limit_error)

    events = _run_agent(a, fake, monkeypatch)

    assert fake.calls == 1
    assert len(events) == 1
    assert events[0].type == "error"
