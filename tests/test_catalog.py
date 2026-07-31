"""Tests for the LiteLLM-backed catalog: text-only filtering, provider gating,
and default-model selection. LiteLLM's registry/provider_list are monkeypatched
so tests are hermetic and don't depend on the installed litellm version.
"""

import types

import pytest

from opendot import catalog

FAKE_MODEL_COST = {
    "sample_spec": {"mode": "chat", "litellm_provider": "openai"},
    "gpt-4o": {"mode": "chat", "litellm_provider": "openai"},
    "dall-e-3": {"mode": "image_generation", "litellm_provider": "openai"},
    "whisper-1": {"mode": "audio_transcription", "litellm_provider": "openai"},
    # bare key (as LiteLLM's registry actually stores it) — must get prefixed
    "deepseek-chat": {"mode": "chat", "litellm_provider": "deepseek"},
    "obscure/model": {
        "mode": "chat",
        "litellm_provider": "obscureproxy",
    },  # not routable
}


class _FakeLiteLLM(types.SimpleNamespace):
    pass


@pytest.fixture(autouse=True)
def fake_litellm(monkeypatch):
    fake = _FakeLiteLLM(
        model_cost=FAKE_MODEL_COST,
        provider_list=["openai", "deepseek"],  # obscureproxy NOT routable
    )
    monkeypatch.setattr(catalog, "_litellm", lambda: fake)


def test_list_models_text_only():
    models = {m["model"] for m in catalog.list_models()}
    # openai/anthropic bare names resolve in LiteLLM, so they stay familiar;
    # other providers get prefixed so they're routable.
    assert "gpt-4o" in models
    assert "deepseek/deepseek-chat" in models
    assert "dall-e-3" not in models  # image
    assert "whisper-1" not in models  # audio
    assert "sample_spec" not in models


def test_bare_provider_model_is_prefixed_for_routing():
    """Regression: a provider whose bare model id LiteLLM can't resolve (e.g.
    deepseek) must be returned prefixed, or auto-switch produces an unroutable
    'LLM Provider NOT provided' string. openai/anthropic stay bare."""
    by_name = {m["name"]: m["model"] for m in catalog.list_models()}
    assert by_name["gpt-4o"] == "gpt-4o"  # bare-ok provider, untouched
    # a bare non-openai/anthropic key gets prefixed so it's routable
    assert by_name["deepseek-chat"] == "deepseek/deepseek-chat"


def test_list_models_only_routable_providers():
    models = {m["model"] for m in catalog.list_models()}
    assert "obscure/model" not in models  # provider not in provider_list


def test_list_providers():
    provs = {p["name"]: p["env"] for p in catalog.list_providers()}
    assert provs.get("OpenAI") == "OPENAI_API_KEY"
    assert provs.get("Deepseek") == "DEEPSEEK_API_KEY"


def test_default_model_for_env():
    assert catalog.default_model_for_env("OPENAI_API_KEY") == "gpt-4o"
    assert catalog.default_model_for_env("DEEPSEEK_API_KEY") == "deepseek/deepseek-chat"


def test_unavailable_litellm_returns_empty(monkeypatch):
    def boom():
        raise ImportError("no litellm")

    monkeypatch.setattr(catalog, "_litellm", boom)
    assert catalog.list_models() == []
    assert catalog.list_providers() == []
