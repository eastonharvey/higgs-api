"""End to end: budget gate -> pooled idempotent submission -> registry.

    python -m hf_pipeline.run shots.json --ceiling 5.00

shots.json: [{"shot_id": "ep01_sc01_sh01", "prompt": "...", "kind": "video",
              "tier": "standard", "scene": "sc01", "episode": "ep01",
              "overrides": {"duration": 8}}, ...]

Results arrive at the webhook receiver (hf_pipeline.webhook), which downloads
outputs and writes the ledger. If HF_WEBHOOK_URL is unset, submissions still
work; poll with higgsfield_client.status(request_id) as recovery.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import load_settings
from .estimate import BudgetExceeded, budget_gate
from .ledger import Registry
from .models import load_models, resolve_shot
from .pool import run_pool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", type=Path)
    ap.add_argument("--ceiling", required=True, help="USD ceiling for the whole batch")
    ap.add_argument("--yes", action="store_true", help="submit without the interactive confirm")
    args = ap.parse_args(argv)

    settings = load_settings()
    models = load_models(settings.models_path)
    specs = json.loads(args.shots.read_text())
    shots = [
        resolve_shot(s["shot_id"], s["prompt"], kind=s.get("kind", "video"), tier=s.get("tier", "standard"),
                     scene=s.get("scene", ""), episode=s.get("episode", ""), models=models,
                     **s.get("overrides", {}))
        for s in specs
    ]

    try:
        estimates = budget_gate(shots, args.ceiling)
    except BudgetExceeded as e:
        print(f"ABORT: {e}")
        for ln in e.lines:
            print(f"  {ln.shot_id:<24}{ln.model_id:<44}${ln.usd}")
        return 2

    total = sum(e.usd for e in estimates)
    print(f"Estimated ${total} for {len(shots)} shot(s), ceiling ${args.ceiling}")
    for ln in estimates:
        print(f"  {ln.shot_id:<24}{ln.model_id:<44}${ln.usd}{'  ~approx' if ln.approximate else ''}")
    if not args.yes and input("Submit? [y/N] ").strip().lower() != "y":
        print("Not submitted.")
        return 1

    registry = Registry(settings.output_dir / "registry.sqlite")
    est_by_id = {e.shot_id: e for e in estimates}
    shot_by_id = {s.shot_id: s for s in shots}

    def on_submitted(res):
        registry.record_submission(res, shot_by_id[res.shot_id], est_by_id.get(res.shot_id))
        print(f"submitted {res.shot_id} -> {res.request_id} ({res.accepted_status})")

    report = asyncio.run(run_pool(shots, settings=settings, on_submitted=on_submitted))
    for sid, err in report.failed.items():
        print(f"FAILED to submit {sid}: {err}")
    print(f"{len(report.submitted)} submitted, {len(report.failed)} failed")
    return 0 if not report.failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
