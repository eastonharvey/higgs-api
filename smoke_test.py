"""Smoke test: estimate one Seedance 2.5 shot and one Soul v2 image. Submits nothing.

    python smoke_test.py

Hits POST /estimate/{model_id} twice. Estimates are not generations and are
not billed as generations. Exit 0 only if both estimates return credits+usd.
"""

from __future__ import annotations

import sys

from hf_pipeline import budget_gate, load_settings, resolve_shot
from hf_pipeline.estimate import BudgetExceeded


def main() -> int:
    settings = load_settings()  # loads .env.local, verifies HF_KEY is present
    shots = [
        resolve_shot("smoke_seedance_720p_5s", "A cinematic scene at sunset",
                     kind="video", tier="standard", duration=5, resolution="720p", aspect_ratio="16:9"),
        resolve_shot("smoke_soul_v2_sheet", "Editorial portrait in soft daylight",
                     kind="image", tier="sheet"),
    ]
    assert shots[0].model_id == "bytedance/seedance-2.5/text-to-video"
    assert shots[1].model_id == "higgsfield-ai/soul/v2/standard"

    try:
        lines = budget_gate(shots, ceiling_usd="1000")  # high ceiling: we only want the numbers
    except BudgetExceeded as e:
        print(f"unexpected: {e}", file=sys.stderr)
        return 1

    for ln in lines:
        tag = "  (approximate: " + ln.note + ")" if ln.approximate else ""
        print(f"{ln.shot_id:<28}{ln.model_id:<44}credits={ln.credits}  usd={ln.usd}{tag}")
    print(f"total usd={sum(l.usd for l in lines)}  (nothing submitted; concurrency={settings.concurrency})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
