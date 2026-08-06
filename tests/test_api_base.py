"""Tests for the ``--api-base`` local-server path (llama.cpp / vLLM / LM Studio).

opendot can point at any OpenAI-compatible local server via ``AgentConfig.api_base``
(the ``--api-base`` flag). Two behaviours are user-visible but had no regression
protection (a search of ``tests/`` finds no ``api_base`` references):

1. ``api_base`` propagates into **both** ``litellm.acompletion()`` calls — the
   streaming turn and the non-streaming fallback — so requests actually reach the
   configured local server (and defaults to ``None`` => provider default).
2. Setting ``api_base`` **bypasses provider auto-detection** in ``_build_agent``:
   a local server needs no API key, so opendot must not switch models when the
   chosen model's provider key is missing.

Completion calls are stubbed with a fake LiteLLM, so no real server is contacted.
Patterns mirror ``tests/test_usage.py`` (bare-agent + fake LiteLLM).
"""

import sys
import types

from opendot import cli
from opendot.agent.config import AgentConfig
from opendot.agent.loop import Agent
from opendot.agent.usage import Usage

API_BASE = "http://localhost:8080/v1"


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _RecordingLiteLLM(types.SimpleNamespace):
    """Fake LiteLLM that records every ``acompletion`` call's kwargs and returns a
    minimal streaming/non-streaming response — no real server involved."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    async def acompletion(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):

            async def gen():
                # a single usage-only chunk (no choices) ends the turn cleanly
                yield types.SimpleNamespace(choices=[], usage=_Usage())

            return gen()
        # non-streaming: one message, no tool calls
        msg = types.SimpleNamespace(content="ok", tool_calls=None)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice], usage=_Usage())

    def cost_per_token(self, model, prompt_tokens, completion_tokens):
        return (0.0, 0.0)


def _bare_agent(api_base):
    a = Agent.__new__(Agent)
    a.config = AgentConfig(model="gpt-4o", workdir="/tmp", api_base=api_base)
    a.usage = Usage()
    a.messages = []
    return a


async def test_api_base_propagates_into_streaming_completion():
    a = _bare_agent(API_BASE)
    fake = _RecordingLiteLLM()
    async for _ in a._stream_turn(fake, []):
        pass
    assert len(fake.calls) == 1
    assert fake.calls[0]["api_base"] == API_BASE
    assert fake.calls[0]["model"] == "gpt-4o"
    assert fake.calls[0]["stream"] is True


async def test_api_base_propagates_into_nonstreaming_completion():
    a = _bare_agent(API_BASE)
    fake = _RecordingLiteLLM()
    async for _ in a._nonstream_turn(fake, []):
        pass
    assert len(fake.calls) == 1
    assert fake.calls[0]["api_base"] == API_BASE
    assert fake.calls[0]["stream"] is False


async def test_api_base_defaults_to_none_nonstreaming():
    """No ``--api-base`` => ``api_base=None`` reaches acompletion (provider default)."""
    a = _bare_agent(None)
    fake = _RecordingLiteLLM()
    async for _ in a._nonstream_turn(fake, []):
        pass
    assert fake.calls[0]["api_base"] is None


async def test_api_base_defaults_to_none_streaming():
    """Streaming counterpart: no ``--api-base`` => ``api_base=None`` reaches the
    streaming acompletion call too."""
    a = _bare_agent(None)
    fake = _RecordingLiteLLM()
    async for _ in a._stream_turn(fake, []):
        pass
    assert fake.calls[0]["api_base"] is None


def test_api_base_bypasses_provider_autodetect(tmp_path, monkeypatch):
    """With ``api_base`` set, ``_build_agent`` must NOT consult provider
    auto-detection and must keep the chosen model (local server needs no key)."""
    from opendot import cli, mcp, providers

    called = {"env_var_for": 0, "model_for_available_key": 0}

    def _env(model):
        called["env_var_for"] += 1
        return "OPENAI_API_KEY"

    def _switch():
        called["model_for_available_key"] += 1
        return "claude-sonnet-4-5"

    monkeypatch.setattr(providers, "env_var_for", _env)
    monkeypatch.setattr(providers, "model_for_available_key", _switch)
    monkeypatch.setattr(mcp, "load_mcp_config", lambda: None)  # keep it hermetic
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = cli._build_agent("gpt-4o", str(tmp_path), api_base=API_BASE)

    assert agent.config.api_base == API_BASE
    assert agent.config.model == "gpt-4o"  # not switched
    assert called["env_var_for"] == 0  # auto-detect skipped entirely
    assert called["model_for_available_key"] == 0


def test_no_api_base_triggers_provider_autodetect(tmp_path, monkeypatch):
    """Contrast: without ``api_base``, a missing key for the chosen model DOES
    trigger the auto-switch — proving the bypass above is meaningful."""
    from opendot import cli, mcp, providers

    monkeypatch.setattr(providers, "env_var_for", lambda model: "OPENAI_API_KEY")
    monkeypatch.setattr(providers, "model_for_available_key", lambda: "claude-sonnet-4-5")
    monkeypatch.setattr(mcp, "load_mcp_config", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = cli._build_agent("gpt-4o", str(tmp_path), api_base=None)

    assert agent.config.model == "claude-sonnet-4-5"  # switched via auto-detect


def test_spurious_warning_for_local_api_base(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["opendot", "--api-base", API_BASE, "--model", "openai/local-model", "--repl"],
    )

    called = []

    def fake_warn_if_missing_key(model):
        called.append(model)

    monkeypatch.setattr(cli, "_warn_if_missing_key", fake_warn_if_missing_key)

    class FakeStdin:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", FakeStdin())

    dummy_object = "dummy"
    monkeypatch.setattr(cli, "_build_agent", lambda model, workdir, confirm, api_base: dummy_object)

    monkeypatch.setattr(cli, "_interactive", lambda agent: None)

    cli.main()

    assert called == []


def test_warning_for_missing_key_with_no_api_base(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # --api-base defaults to $OPENAI_API_BASE via argparse; clear it so the test
    # is hermetic (otherwise a dev/CI machine with it set would skip the warning).
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(sys, "argv", ["opendot", "--model", "gpt-4o", "--repl"])

    called = []

    def fake_warn_if_missing_key(model):
        called.append(model)

    monkeypatch.setattr(cli, "_warn_if_missing_key", fake_warn_if_missing_key)

    class FakeStdin:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", FakeStdin())

    dummy_object = "dummy"
    monkeypatch.setattr(cli, "_build_agent", lambda model, workdir, confirm, api_base: dummy_object)

    monkeypatch.setattr(cli, "_interactive", lambda agent: None)

    cli.main()

    assert called == ["gpt-4o"]
