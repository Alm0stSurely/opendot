"""Tests for the provider/model helpers behind the /model and /provider pickers.

These lock in the model→env-var mapping (used for the missing-key hint and the
connect flow) and that model discovery reads from LiteLLM's registry.
"""

import os

from opendot import providers as p


def test_env_var_for_known_providers():
    assert p.env_var_for("gpt-5.1") == "OPENAI_API_KEY"
    assert p.env_var_for("claude-opus-4-5") == "ANTHROPIC_API_KEY"
    assert p.env_var_for("deepseek/deepseek-chat") == "DEEPSEEK_API_KEY"
    assert p.env_var_for("gemini/gemini-3-pro") == "GEMINI_API_KEY"
    assert p.env_var_for("huggingface/together/x") == "HF_TOKEN"


def test_env_var_for_keyless_local_models():
    assert p.env_var_for("ollama/qwen3") is None
    assert p.env_var_for("lm_studio/foo") is None


def test_env_var_for_unknown():
    assert p.env_var_for("some-unknown-model") is None


def test_provider_of_grouping():
    assert p.provider_of("deepseek/deepseek-chat") == "deepseek"
    assert p.provider_of("gpt-5.1") == "openai"
    assert p.provider_of("claude-opus-4-5") == "anthropic"


def test_list_models_from_registry():
    models = p.list_models()
    # LiteLLM ships a large registry; if it's importable we expect real entries.
    assert isinstance(models, list)
    if models:  # only assert content when litellm is present
        assert "sample_spec" not in models
        assert all(isinstance(m, str) and m for m in models)


def test_register_key_sets_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    p.register_key("DEEPSEEK_API_KEY", "sk-xyz")
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-xyz"
