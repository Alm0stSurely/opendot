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


@dataclass
class AgentConfig:
    """Everything the agent loop needs. Kept minimal for v1."""

    model: str = DEFAULT_MODEL
    workdir: str = field(default_factory=os.getcwd)
    max_steps: int = 40  # hard bound on tool-calling turns per user message
    temperature: float | None = None
    system_prompt: str | None = None  # None => use the built-in default
    # Base URL for an OpenAI-compatible server (llama.cpp/llama-server, vLLM,
    # LM Studio, …). Falls back to $OPENAI_API_BASE / the provider default.
    api_base: str | None = None
