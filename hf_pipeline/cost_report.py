"""Print spend from the cost ledger, grouped per scene, per episode, and per tier.

    python -m hf_pipeline.cost_report [path/to/cost_ledger.csv]

charged_usd is only non zero for completed requests (failed/nsfw are refunded).
est_usd totals are shown alongside so you can see what was attempted vs billed.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .config import load_settings


def _group(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, Decimal | int]]:
    g: dict[str, dict[str, Decimal | int]] = defaultdict(lambda: {"est": Decimal(0), "charged": Decimal(0), "n": 0, "completed": 0})
    for r in rows:
        k = r.get(key) or "(none)"
        g[k]["est"] += Decimal(r.get("est_usd") or "0")
        g[k]["charged"] += Decimal(r.get("charged_usd") or "0")
        g[k]["n"] += 1
        if r.get("status") == "completed":
            g[k]["completed"] += 1
    return dict(g)


def _print_table(title: str, table: dict[str, dict[str, Decimal | int]]) -> None:
    print(f"\n{title}")
    print(f"{'key':<24}{'requests':>9}{'completed':>11}{'est_usd':>12}{'charged_usd':>14}")
    for k in sorted(table):
        v = table[k]
        print(f"{k:<24}{v['n']:>9}{v['completed']:>11}{v['est']:>12.3f}{v['charged']:>14.3f}")
    tot_est = sum((v["est"] for v in table.values()), Decimal(0))
    tot_ch = sum((v["charged"] for v in table.values()), Decimal(0))
    print(f"{'TOTAL':<24}{sum(v['n'] for v in table.values()):>9}"
          f"{sum(v['completed'] for v in table.values()):>11}{tot_est:>12.3f}{tot_ch:>14.3f}")


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else load_settings(need_credentials=False).ledger_path
    rows = load_rows(path)
    if not rows:
        print(f"No ledger rows at {path}")
        return 0
    print(f"Ledger: {path}  ({len(rows)} rows)")
    _print_table("Per scene (episode/scene)", _group([{**r, "ep_scene": f"{r.get('episode') or '-'}/{r.get('scene') or '-'}"} for r in rows], "ep_scene"))
    _print_table("Per episode", _group(rows, "episode"))
    _print_table("Per tier", _group(rows, "tier"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
