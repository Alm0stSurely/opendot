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

    def add_response(self, resp, litellm) -> None:
        """Fold one LiteLLM response's usage + cost into the running totals."""
        try:
            u = getattr(resp, "usage", None)
            if u:
                self.prompt_tokens += int(getattr(u, "prompt_tokens", 0) or 0)
                self.completion_tokens += int(getattr(u, "completion_tokens", 0) or 0)
                self.total_tokens += int(getattr(u, "total_tokens", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.cost_usd += float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:  # noqa: BLE001
            pass
