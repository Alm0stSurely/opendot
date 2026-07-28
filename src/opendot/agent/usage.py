"""Token + cost accounting for a session.

LiteLLM computes per-response cost via ``litellm.completion_cost`` and reports
token counts in the response usage. We accumulate both across a session so the
TUI sidebar can show "N tokens · $X spent" like the tools people expect.

Best-effort: if a provider/model has no cost data, cost stays 0 but tokens still
accumulate. Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add_response(self, resp, litellm, model: str | None = None) -> None:
        """Fold one LiteLLM response's usage + cost into the running totals.

        Cost is computed from the token counts + model (works for stream chunks),
        with completion_cost as a fallback — completion_cost often fails on raw
        stream chunks, which is why streamed turns showed $0."""
        p = c = 0
        try:
            u = getattr(resp, "usage", None)
            if u:
                p = int(getattr(u, "prompt_tokens", 0) or 0)
                c = int(getattr(u, "completion_tokens", 0) or 0)
                self.prompt_tokens += p
                self.completion_tokens += c
                self.total_tokens += int(getattr(u, "total_tokens", 0) or 0) or (p + c)
        except Exception:  # noqa: BLE001
            pass
        # Prefer token-based cost (reliable for stream chunks); else try the
        # whole-response helper.
        added = False
        if model and (p or c):
            try:
                pc, cc = litellm.cost_per_token(model=model, prompt_tokens=p, completion_tokens=c)
                self.cost_usd += float(pc or 0.0) + float(cc or 0.0)
                added = True
            except Exception:  # noqa: BLE001
                pass
        if not added:
            try:
                self.cost_usd += float(litellm.completion_cost(completion_response=resp) or 0.0)
            except Exception:  # noqa: BLE001
                pass
