"""Token + cost accounting for a session.

LiteLLM computes per-response cost via ``litellm.completion_cost`` and reports
token counts in the response usage. We accumulate both across a session so the
TUI sidebar can show "N tokens · $X spent" like the tools people expect.

Best-effort: if a provider/model has no cost data, cost stays 0 but tokens still
accumulate. Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CallRecord:
    """One model call, for the per-call trace (see ``opendot trace``)."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_s: float | None = None


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    #: One entry per model call, in order — powers the per-call trace. The
    #: totals above stay the source of truth for the sidebar; this is additive.
    calls: list[CallRecord] = field(default_factory=list)

    def add_response(
        self, resp, litellm, model: str | None = None, latency_s: float | None = None
    ) -> None:
        """Fold one LiteLLM response's usage + cost into the running totals, and
        record it as a per-call entry for the trace.

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
        except Exception:  # noqa: BLE001,S110 - accounting is best-effort, never fatal
            pass
        # Prefer token-based cost (reliable for stream chunks); else try the
        # whole-response helper.
        call_cost = 0.0
        added = False
        if model and (p or c):
            try:
                pc, cc = litellm.cost_per_token(model=model, prompt_tokens=p, completion_tokens=c)
                call_cost = float(pc or 0.0) + float(cc or 0.0)
                self.cost_usd += call_cost
                added = True
            except Exception:  # noqa: BLE001
                pass
        if not added:
            try:
                call_cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
                self.cost_usd += call_cost
            except Exception:  # noqa: BLE001
                pass
        self.calls.append(
            CallRecord(
                model=model or "?",
                prompt_tokens=p,
                completion_tokens=c,
                cost_usd=call_cost,
                latency_s=latency_s,
            )
        )

    def trace_lines(self) -> list[str]:
        """Plain-text per-call breakdown for ``/trace``: one line per model call
        (index, model, prompt/completion tokens, cost, latency), then a total."""
        if not self.calls:
            return ["no model calls yet this session"]
        lines = []
        for i, r in enumerate(self.calls, 1):
            lat = f"{r.latency_s:.2f}s" if r.latency_s is not None else "?"
            lines.append(
                f"{i:>2}. {r.model}  in {r.prompt_tokens} / out {r.completion_tokens} tok  "
                f"${r.cost_usd:.4f}  {lat}"
            )
        lines.append(
            f"    total: {self.total_tokens} tok  ${self.cost_usd:.4f}  "
            f"across {len(self.calls)} call(s)"
        )
        return lines
