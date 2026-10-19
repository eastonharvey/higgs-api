"""Cost estimation and the budget gate.

POST https://api.higgsfield.ai/estimate/{model_id} with the exact body you
intend to submit. Two response shapes have been observed:

  1. Exact (documented):   {"credits": "1.500", "usd": "0.094"}
  2. Description (seen live for bytedance/seedance-2.5/text-to-video):
       {"type": "description",
        "pricing_description": "... costs roughly $0.2056 per second of generated
         video at 480p, $0.4622 at 720p, and $1.1372 at 1080p. ..."}

For shape 2 we derive usd = rate[resolution] * duration from the text and mark
the Estimate `approximate=True`. The ledger's charged_usd for such shots is
therefore an estimate, not an invoice; reconcile against the console.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from .http import post_json_sync
from .shots import Shot

# Only the sentence that states per-second rates; the token-rate sentence must not bleed in.
_PER_SEC_SENTENCE_RE = re.compile(r"\$[0-9.]+\s*per second.*?(?:\.(?=\s+[A-Z])|\.$|$)", re.IGNORECASE | re.DOTALL)
_RATE_RE = re.compile(r"\$([0-9]+(?:\.[0-9]+)?)\s*(?:per second[^$]*?)?at\s*(\d{3,4}p)", re.IGNORECASE)


class BudgetExceeded(Exception):
    def __init__(self, total_usd: Decimal, ceiling_usd: Decimal, lines: list["Estimate"]):
        self.total_usd = total_usd
        self.ceiling_usd = ceiling_usd
        self.lines = lines
        super().__init__(
            f"Estimated spend ${total_usd} exceeds ceiling ${ceiling_usd} "
            f"across {len(lines)} shot(s); nothing was submitted."
        )


class EstimateUnavailable(Exception):
    """The estimate endpoint answered but we cannot turn it into a number."""


@dataclass(frozen=True)
class Estimate:
    model_id: str
    credits: Optional[Decimal]
    usd: Decimal
    shot_id: Optional[str] = None
    approximate: bool = False
    note: str = ""

    @classmethod
    def from_response(cls, model_id: str, data: dict[str, Any], body: Optional[dict[str, Any]] = None,
                      shot_id: Optional[str] = None) -> "Estimate":
        if isinstance(data, dict) and "usd" in data:
            try:
                return cls(
                    model_id=model_id,
                    credits=Decimal(str(data["credits"])) if data.get("credits") is not None else None,
                    usd=Decimal(str(data["usd"])),
                    shot_id=shot_id,
                )
            except (InvalidOperation, TypeError) as e:
                raise ValueError(f"Unexpected estimate response for {model_id}: {data!r}") from e

        if isinstance(data, dict) and data.get("type") == "description":
            return cls._from_description(model_id, str(data.get("pricing_description", "")), body or {}, shot_id)

        raise ValueError(f"Unexpected estimate response for {model_id}: {data!r}")

    @classmethod
    def _from_description(cls, model_id: str, text: str, body: dict[str, Any], shot_id: Optional[str]) -> "Estimate":
        m = _PER_SEC_SENTENCE_RE.search(text)
        rates = {res.lower(): Decimal(rate) for rate, res in _RATE_RE.findall(m.group(0))} if m else {}
        if not rates:
            raise EstimateUnavailable(
                f"{model_id}: estimate endpoint returned a pricing description with no parseable "
                f"per-second rates. Text: {text[:200]!r}"
            )
        res = str(body.get("resolution", "")).lower()
        if res not in rates:
            raise EstimateUnavailable(
                f"{model_id}: no per-second rate for resolution {res!r}; parsed rates for {sorted(rates)}"
            )
        try:
            duration = Decimal(str(body.get("duration", 5)))
        except InvalidOperation as e:
            raise EstimateUnavailable(f"{model_id}: non numeric duration {body.get('duration')!r}") from e
        usd = (rates[res] * duration).quantize(Decimal("0.0001"))
        return cls(
            model_id=model_id, credits=None, usd=usd, shot_id=shot_id, approximate=True,
            note=f"derived: ${rates[res]}/s at {res} x {duration}s (provider says 'roughly'; aspect ratio may change it)",
        )


def estimate(model_id: str, body: dict[str, Any]) -> Estimate:
    """Estimate one request. Never submits; the estimate endpoint is not a generation."""
    if not model_id or model_id.startswith("/"):
        raise ValueError("model_id must be a path style id like 'bytedance/seedance-2.5/text-to-video'")
    data = post_json_sync(f"/estimate/{model_id}", body)
    return Estimate.from_response(model_id, data, body)


def budget_gate(shots: Iterable[Shot], ceiling_usd: float | str | Decimal) -> list[Estimate]:
    """Estimate every shot up front. Returns the per shot estimates if the total
    fits under ceiling_usd, otherwise raises BudgetExceeded before anything is
    submitted. Approximate estimates count at face value; if you want headroom
    for them, lower the ceiling. Partial submission is never allowed through."""
    ceiling = Decimal(str(ceiling_usd))
    if ceiling <= 0:
        raise ValueError("ceiling_usd must be positive")

    lines: list[Estimate] = []
    for shot in shots:
        est = estimate(shot.model_id, shot.body)
        lines.append(Estimate(est.model_id, est.credits, est.usd, shot_id=shot.shot_id,
                              approximate=est.approximate, note=est.note))

    total = sum((e.usd for e in lines), Decimal("0"))
    if total > ceiling:
        raise BudgetExceeded(total, ceiling, lines)
    return lines
