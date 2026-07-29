"""Model & provider catalog — sourced entirely from LiteLLM.

opendot invokes every model through LiteLLM, so LiteLLM is the source of truth
for what's available and routable:

* **Providers** come from ``litellm.provider_list`` — the ~150 providers LiteLLM
  can actually route to. We only offer these in ``/provider`` so a key the user
  enters will actually work.
* **Models** come from LiteLLM's registry (``litellm.model_cost``), filtered to
  those providers and to text chat models only (``mode`` in {chat, completion,
  responses}) — no image / audio / video / embedding models.

No network, no external database, no hardcoded model strings.
"""

from __future__ import annotations

# Text-generation modes we surface; everything else (image_generation,
# audio_*, video_generation, embedding, rerank, …) is filtered out.
_TEXT_MODES = {"chat", "completion", "responses"}

# Providers whose bare model ids LiteLLM resolves without a "provider/" prefix
# (so "gpt-4o" / "claude-..." keep their familiar names). Every other provider's
# bare id must be prefixed or LiteLLM errors with "LLM Provider NOT provided".
_BARE_OK_PROVIDERS = {"openai", "anthropic"}


def _litellm():
    import litellm
    return litellm


def provider_ids() -> set[str]:
    """LiteLLM's routable provider ids (lowercased). Empty if unavailable."""
    try:
        lst = _litellm().provider_list
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for p in lst:
        val = getattr(p, "value", None) or str(p)
        if val:
            out.add(val.lower())
    return out


def _pretty(provider_id: str) -> str:
    """A display name for a provider id (best-effort title-casing)."""
    special = {
        "openai": "OpenAI", "xai": "xAI", "ai21": "AI21",
        "openrouter": "OpenRouter", "huggingface": "Hugging Face",
    }
    if provider_id in special:
        return special[provider_id]
    return provider_id.replace("_", " ").replace("-", " ").title()


def list_models() -> list[dict]:
    """Text chat models from LiteLLM's registry, grouped by provider.

    Returns [{model, name, provider}] where ``model`` is the string to pass to
    LiteLLM. Empty list if LiteLLM is unavailable.
    """
    try:
        mc = _litellm().model_cost
    except Exception:  # noqa: BLE001
        return []
    routable = provider_ids()
    out: list[dict] = []
    seen: set[str] = set()
    for name, meta in mc.items():
        if not name or name == "sample_spec" or not isinstance(meta, dict):
            continue
        if meta.get("mode") not in _TEXT_MODES:
            continue
        prov = (meta.get("litellm_provider") or "").lower()
        if routable and prov not in routable:
            continue
        # LiteLLM resolves bare names only for a few providers (openai,
        # anthropic); for the rest a bare key like "deepseek-chat" errors with
        # "LLM Provider NOT provided". Prefix those to get a routable string
        # ("deepseek/deepseek-chat") while leaving familiar names (gpt-4o,
        # claude-...) untouched.
        needs_prefix = "/" not in name and prov and prov not in _BARE_OK_PROVIDERS
        model = f"{prov}/{name}" if needs_prefix else name
        if model in seen:  # registry may list both bare and prefixed variants
            continue
        seen.add(model)
        out.append({"model": model, "name": name, "provider": _pretty(prov) if prov else "other"})
    out.sort(key=lambda d: (d["provider"].lower(), d["name"].lower()))
    return out


def list_providers() -> list[dict]:
    """Providers that (a) LiteLLM can route to and (b) have at least one text
    chat model in the registry. Returns [{name, env}] sorted by name.

    The env-var name is resolved by ``providers.env_var_for`` on a sample model
    from that provider, so we never duplicate that mapping here."""
    from opendot.providers import env_var_for

    models = list_models()
    # provider display name -> a sample model string, to derive its env var.
    sample: dict[str, str] = {}
    for m in models:
        sample.setdefault(m["provider"], m["model"])
    out: list[dict] = []
    for pname, model in sample.items():
        var = env_var_for(model)
        if var:  # keyless/local providers aren't a "connect a key" flow
            out.append({"name": pname, "env": var})
    out.sort(key=lambda d: d["name"].lower())
    return out


def default_model_for_env(env_var: str) -> str | None:
    """A model string whose provider uses ``env_var`` — for auto-picking a
    working model from a key the user has set. Prefers a shorter/cleaner id
    (usually the canonical one). None if no text model matches."""
    from opendot.providers import env_var_for

    best = None
    for m in list_models():
        if env_var_for(m["model"]) == env_var:
            if best is None or len(m["model"]) < len(best):
                best = m["model"]
    return best
