"""Tests for agent configuration defaults and env-var overrides."""

from opendot.agent.config import AgentConfig, _max_retries, _max_steps


def test_max_steps_default_when_unset(monkeypatch):
    """Without OPENDOT_MAX_STEPS, the cap stays at the original default of 40."""
    monkeypatch.delenv("OPENDOT_MAX_STEPS", raising=False)
    assert _max_steps() == 40


def test_max_steps_honors_valid_env_override(monkeypatch):
    monkeypatch.setenv("OPENDOT_MAX_STEPS", "100")
    assert _max_steps() == 100


def test_max_steps_falls_back_on_invalid_or_nonpositive(monkeypatch):
    """Non-integer and non-positive values keep the default of 40."""
    for bad in ("not-a-number", "0", "-10"):
        monkeypatch.setenv("OPENDOT_MAX_STEPS", bad)
        assert _max_steps() == 40, f"{bad!r} should fall back to the default"


def test_agent_config_default_max_steps(monkeypatch):
    monkeypatch.delenv("OPENDOT_MAX_STEPS", raising=False)
    cfg = AgentConfig()
    assert cfg.max_steps == 40


def test_agent_config_env_override(monkeypatch):
    monkeypatch.setenv("OPENDOT_MAX_STEPS", "200")
    cfg = AgentConfig()
    assert cfg.max_steps == 200


def test_agent_config_explicit_argument_overrides_env(monkeypatch):
    """A value passed at construction time takes precedence over the env default."""
    monkeypatch.setenv("OPENDOT_MAX_STEPS", "999")
    cfg = AgentConfig(max_steps=50)
    assert cfg.max_steps == 50


def test_max_retries_default_when_unset(monkeypatch):
    """Without OPENDOT_MAX_RETRIES, the cap stays at the default of 3."""
    monkeypatch.delenv("OPENDOT_MAX_RETRIES", raising=False)
    assert _max_retries() == 3


def test_max_retries_honors_valid_env_override(monkeypatch):
    monkeypatch.setenv("OPENDOT_MAX_RETRIES", "5")
    assert _max_retries() == 5


def test_max_retries_falls_back_on_invalid_or_negative(monkeypatch):
    """Non-integer and negative values keep the default of 3."""
    for bad in ("not-a-number", "-1"):
        monkeypatch.setenv("OPENDOT_MAX_RETRIES", bad)
        assert _max_retries() == 3, f"{bad!r} should fall back to the default"


def test_max_retries_allows_zero(monkeypatch):
    """Zero is a valid, meaningful value (no retries) unlike max_steps."""
    monkeypatch.setenv("OPENDOT_MAX_RETRIES", "0")
    assert _max_retries() == 0


def test_agent_config_default_max_retries(monkeypatch):
    monkeypatch.delenv("OPENDOT_MAX_RETRIES", raising=False)
    cfg = AgentConfig()
    assert cfg.max_retries == 3


def test_agent_config_env_override_max_retries(monkeypatch):
    monkeypatch.setenv("OPENDOT_MAX_RETRIES", "7")
    cfg = AgentConfig()
    assert cfg.max_retries == 7


def test_agent_config_explicit_max_retries_overrides_env(monkeypatch):
    monkeypatch.setenv("OPENDOT_MAX_RETRIES", "999")
    cfg = AgentConfig(max_retries=1)
    assert cfg.max_retries == 1
