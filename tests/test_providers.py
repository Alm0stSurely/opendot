"""Tests for the provider/model helpers behind the /model and /provider pickers.

These lock in the model→env-var mapping (used for the missing-key hint and the
connect flow) and that model discovery reads from LiteLLM's registry.
"""

import os

from opendot import providers as p


def _clear_keys(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "DEEPSEEK_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_model_for_available_key_picks_the_set_provider(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    assert p.model_for_available_key() == "deepseek/deepseek-chat"


def test_model_for_available_key_none_when_no_keys(monkeypatch):
    _clear_keys(monkeypatch)
    assert p.model_for_available_key() is None


def test_model_for_available_key_prefers_openai(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    assert p.model_for_available_key() == "gpt-5.1"


def test_model_for_available_key_handles_huggingface(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    model = p.model_for_available_key()
    assert model is not None and model.startswith("huggingface/")


def test_every_connectable_provider_has_an_auto_default():
    """Every provider you can connect via /provider must map to a default model,
    else setting only that key would leave opendot stuck on gpt-5.1."""
    auto_vars = {var for var, _ in p._ENV_DEFAULT_MODEL}
    for _name, var in p.CONNECTABLE_PROVIDERS:
        assert var in auto_vars, f"{var} has no auto-switch default model"


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
