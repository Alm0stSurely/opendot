"""Provider ↔ API-key mapping and auto model selection.

Model/provider *lists* come from LiteLLM (see ``opendot/catalog.py``). This
module holds only the prefix → API-key-env-var mapping, which LiteLLM doesn't
expose directly. It's small and changes rarely.
"""

from __future__ import annotations

import os

# Providers whose key env var does NOT follow the ``<PROVIDER>_API_KEY``
# convention. LiteLLM buries these inside each provider's validate_environment,
# so this small override table is the one thing we hold; every other provider
# (including GPT-via-Azure, OpenRouter, etc.) is derived from the convention.
_ENV_OVERRIDES: dict[str, str] = {
    "huggingface": "HF_TOKEN",
    "gemini": "GEMINI_API_KEY",  # provider id is 'gemini', not 'google'
    "vertex_ai": "GOOGLE_APPLICATION_CREDENTIALS",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "azure": "AZURE_API_KEY",
}

# Local providers that need no API key. LiteLLM lists these in provider_list
# but doesn't flag "keyless", so we name them. Matches LiteLLM's local set.
_KEYLESS_PROVIDERS = {
    "ollama",
    "ollama_chat",
    "vllm",
    "hosted_vllm",
    "lm_studio",
    "llamafile",
}
# Model-string prefixes for those providers (derived — keep in one place).
NO_KEY_PREFIXES = tuple(f"{p}/" for p in sorted(_KEYLESS_PROVIDERS))


def connectable_providers() -> list[tuple[str, str]]:
    """(display name, env var) pairs for the `/provider` flow — the LiteLLM-
    routable providers that have text models, from the catalog."""
    from opendot import catalog

    return [(p["name"], p["env"]) for p in catalog.list_providers()]


def known_key_vars() -> list[str]:
    """API-key env vars for the providers opendot surfaces — used to show which
    keys are set (sidebar) and to order auto model selection. Derived from the
    catalog's connectable providers, falling back to the override set."""
    try:
        from opendot import catalog

        vars_ = [p["env"] for p in catalog.list_providers()]
        if vars_:
            return vars_
    except Exception:  # noqa: BLE001
        pass
    # Catalog unavailable: fall back to the common providers (_AUTO_ORDER covers
    # the <PROVIDER>_API_KEY ones) plus the override exceptions — so a user with
    # e.g. OPENAI_API_KEY still shows up in the sidebar.
    return list(dict.fromkeys(_AUTO_ORDER + list(_ENV_OVERRIDES.values())))


def _env_for_provider_id(provider: str) -> str | None:
    """Map a LiteLLM provider id to its API-key env var. Uses the override table
    for the exceptions, else the ``<PROVIDER>_API_KEY`` convention."""
    if not provider or provider in _KEYLESS_PROVIDERS:
        return None
    if provider in _ENV_OVERRIDES:
        return _ENV_OVERRIDES[provider]
    return f"{provider.upper()}_API_KEY"


# Bare-model routing: LiteLLM sends these prefixes to a canonical provider.
# Used only when a bare model id isn't found in the registry.
_BARE_ROUTING = {
    "gpt-": "openai",
    "o1": "openai",
    "o3": "openai",
    "claude": "anthropic",
}


def _provider_id_for(model: str) -> str | None:
    """The LiteLLM provider id for a model string — a PURE lookup (no side
    effects). ``provider/model`` → the prefix; a bare id → the registry's
    ``litellm_provider``; else the canonical bare-routing rules.

    Deliberately does NOT call ``litellm.get_llm_provider`` — that can trigger
    interactive auth for some providers (e.g. ChatGPT/Codex) and hang."""
    low = model.lower()
    if "/" in low:
        return low.split("/", 1)[0]
    try:
        meta = _model_registry().get(model) or _model_registry().get(low)
        if meta and meta.get("litellm_provider"):
            return meta["litellm_provider"].lower()
    except Exception:  # noqa: BLE001
        pass
    for prefix in sorted(_BARE_ROUTING, key=len, reverse=True):
        if low.startswith(prefix):
            return _BARE_ROUTING[prefix]
    return None


def _model_registry() -> dict:
    try:
        import litellm

        return litellm.model_cost
    except Exception:  # noqa: BLE001
        return {}


def env_var_for(model: str) -> str | None:
    """The API-key env var LiteLLM expects for ``model``, or None if keyless /
    unknown.

    Works for any form (bare ``gpt-4o``, ``azure/gpt-4o``,
    ``openrouter/openai/gpt-4o``, ``deepseek/deepseek-chat``). The env var is the
    ``<PROVIDER>_API_KEY`` convention plus a small override table."""
    if model.lower().startswith(NO_KEY_PREFIXES):
        return None
    return _env_for_provider_id(_provider_id_for(model) or "")


# Preference order for auto-selecting a model when several provider keys are set.
_AUTO_ORDER = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "COHERE_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
]


def model_for_available_key() -> str | None:
    """If some provider's API key is set, return a working model for it (a real
    model from LiteLLM's registry for that provider). None if no known key is
    set. Used to auto-switch when the configured model's key is missing.

    Env vars are tried in _AUTO_ORDER, then any other key we know about."""
    from opendot import catalog

    others = [v for v in known_key_vars() if v not in _AUTO_ORDER]
    for var in _AUTO_ORDER + others:
        if os.environ.get(var):
            model = catalog.default_model_for_env(var)
            if model:
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
    """Real model strings from LiteLLM's registry (text chat models only).

    Returns them sorted; empty list if LiteLLM isn't importable. This is the
    fallback source for the `/model` picker.
    """
    try:
        from opendot import catalog

        return sorted({m["model"] for m in catalog.list_models()})
    except Exception:  # noqa: BLE001 - registry unavailable
        return []


def register_key(env_var: str, value: str) -> None:
    """Set an API key in this process's environment so it takes effect now.

    opendot does not persist secrets to disk — the caller shows the user the
    `export` line to add to their shell profile for persistence.
    """
    os.environ[env_var] = value
