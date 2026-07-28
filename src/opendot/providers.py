"""Provider ↔ API-key mapping and model discovery.

opendot stays model-agnostic: it never ships its own model list. This module is
the one place that (a) maps a model string to the env var LiteLLM expects for
it, and (b) discovers available models from LiteLLM's own registry so the TUI's
`/model` picker shows real, current models without a hardcoded table.
"""

from __future__ import annotations

import os

# Ordered so more specific prefixes win. Maps a model-string prefix to the env
# var LiteLLM reads the key from. Used for the missing-key hint and the
# `/provider` connect flow — never to restrict which models can be used.
PROVIDER_KEYS: dict[str, str] = {
    "anthropic/": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",       # bare `claude-...` routes to Anthropic
    "gemini/": "GEMINI_API_KEY",
    "deepseek/": "DEEPSEEK_API_KEY",
    "groq/": "GROQ_API_KEY",
    "mistral/": "MISTRAL_API_KEY",
    "huggingface/": "HF_TOKEN",
    "openai/": "OPENAI_API_KEY",
    "gpt-": "OPENAI_API_KEY",            # bare `gpt-...` routes to OpenAI
    "o1": "OPENAI_API_KEY",
    "o3": "OPENAI_API_KEY",
}

# Models that run locally / need no key — never warn or prompt for these.
NO_KEY_PREFIXES = ("ollama/", "ollama_chat/", "lm_studio/")

# The providers offered in the `/provider` connect flow: (display name, env var).
# One entry per distinct key; deduped from PROVIDER_KEYS in a stable order.
CONNECTABLE_PROVIDERS: list[tuple[str, str]] = [
    ("OpenAI", "OPENAI_API_KEY"),
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("Google (Gemini)", "GEMINI_API_KEY"),
    ("DeepSeek", "DEEPSEEK_API_KEY"),
    ("Groq", "GROQ_API_KEY"),
    ("Mistral", "MISTRAL_API_KEY"),
    ("Hugging Face", "HF_TOKEN"),
]


# A sensible default model per provider env var, used to auto-pick a working
# model when the configured default's key is missing but this one's key is set.
# Ordered by preference when several keys are present.
# One entry per CONNECTABLE_PROVIDERS env var, so a key for ANY offered
# provider auto-selects a working model (keep these two lists in sync).
_ENV_DEFAULT_MODEL: list[tuple[str, str]] = [
    ("OPENAI_API_KEY", "gpt-5.1"),
    ("ANTHROPIC_API_KEY", "claude-opus-4-5"),
    ("GEMINI_API_KEY", "gemini/gemini-3-pro"),
    ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
    ("GROQ_API_KEY", "groq/llama-3.3-70b-versatile"),
    ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
    ("HF_TOKEN", "huggingface/meta-llama/Llama-3.3-70B-Instruct"),
]


def env_var_for(model: str) -> str | None:
    """The API-key env var LiteLLM expects for ``model``, or None if unknown /
    keyless (local models)."""
    low = model.lower()
    if low.startswith(NO_KEY_PREFIXES):
        return None
    for prefix, var in PROVIDER_KEYS.items():
        if low.startswith(prefix):
            return var
    return None


def model_for_available_key() -> str | None:
    """If some provider's API key is set in the environment, return a sensible
    default model for it (first match by preference order). None if no known key
    is set. Used to auto-switch when the configured model's key is missing."""
    import os
    for var, model in _ENV_DEFAULT_MODEL:
        if os.environ.get(var):
            return model
    return None


def provider_of(model: str) -> str:
    """Group label for a model string, for the `/model` picker's headings.
    Uses the LiteLLM prefix if present (``deepseek/...`` -> ``deepseek``), else
    a best-effort guess from the key map, else ``other``."""
    if "/" in model:
        return model.split("/", 1)[0]
    var = env_var_for(model)
    if var:
        return var.replace("_API_KEY", "").replace("_TOKEN", "").lower()
    return "other"


def list_models() -> list[str]:
    """Real model strings from LiteLLM's registry (``litellm.model_cost``).

    Returns them sorted; empty list if LiteLLM isn't importable. This is the
    source for the `/model` picker — no hardcoded list, updates with LiteLLM.
    """
    try:
        import litellm

        names = [m for m in litellm.model_cost.keys() if m and m != "sample_spec"]
        return sorted(set(names))
    except Exception:  # noqa: BLE001 - registry unavailable
        return []


def register_key(env_var: str, value: str) -> None:
    """Set an API key in this process's environment so it takes effect now.

    opendot does not persist secrets to disk — the caller shows the user the
    `export` line to add to their shell profile for persistence.
    """
    os.environ[env_var] = value
