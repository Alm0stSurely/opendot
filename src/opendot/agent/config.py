"""Agent configuration.

Model IDs are whatever LiteLLM accepts (``gpt-4o``, ``claude-sonnet-4-5``,
``ollama/qwen2.5``, ``huggingface/...``, etc.) — opendot is model-agnostic and
does not maintain its own provider list. API keys are read from the environment
by LiteLLM in the usual way (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# A sensible default that works if the user has an OpenAI key; over/ridden via
# --model, the OPENDOT_MODEL env var, or config.
DEFAULT_MODEL = os.environ.get("OPENDOT_MODEL", "gpt-5.1")


def _max_steps() -> int:
    """Default step cap read from ``OPENDOT_MAX_STEPS``.

    Falls back to 40 when the variable is unset or not a positive integer.
    """
    raw = os.environ.get("OPENDOT_MAX_STEPS", "40")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 40
    return value if value > 0 else 40


def _max_retries() -> int:
    """Default transient-error retry cap read from ``OPENDOT_MAX_RETRIES``.

    Falls back to 3 when the variable is unset or not a non-negative integer.
    0 is a valid value (retries disabled), unlike ``_max_steps``.
    """
    raw = os.environ.get("OPENDOT_MAX_RETRIES", "3")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 3
    return value if value >= 0 else 3


def _max_usd() -> float | None:
    """Default spend cap read from ``OPENDOT_MAX_USD``.

    Falls back to None (unlimited) when the variable is unset or not a positive number.
    """
    raw = os.environ.get("OPENDOT_MAX_USD")
    if raw is None:
        return None
    try:
        value = float(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _max_tokens() -> int | None:
    """Default token cap read from ``OPENDOT_MAX_TOKENS``.

    Falls back to None (unlimited) when the variable is unset or not a positive integer.
    """
    raw = os.environ.get("OPENDOT_MAX_TOKENS")
    if raw is None:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


@dataclass
class AgentConfig:
    """Everything the agent loop needs. Kept minimal for v1."""

    model: str = DEFAULT_MODEL
    workdir: str = field(default_factory=os.getcwd)
    # Hard bound on tool-calling turns per user message. Override via the
    # OPENDOT_MAX_STEPS env var or by passing a value at construction time.
    max_steps: int = field(default_factory=_max_steps)
    # Bound on retries for a transient model-call error (rate-limit/5xx/timeout)
    # before a turn gives up. Override via OPENDOT_MAX_RETRIES.
    max_retries: int = field(default_factory=_max_retries)
    temperature: float | None = None
    system_prompt: str | None = None  # None => use the built-in default
    # Base URL for an OpenAI-compatible server (llama.cpp/llama-server, vLLM,
    # LM Studio, …). Falls back to $OPENAI_API_BASE / the provider default.
    api_base: str | None = None
    # Per-agent spend cap in USD. None means unlimited (default). Set via
    # OPENDOT_MAX_USD env var or the --usd CLI flag.
    max_usd: float | None = field(default_factory=_max_usd)
    # Per-agent token cap. None means unlimited (default). Set via
    # OPENDOT_MAX_TOKENS env var or the --tokens CLI flag.
    max_tokens: int | None = field(default_factory=_max_tokens)
