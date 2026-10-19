"""Submission registry (SQLite) and cost ledger (CSV).

Webhook deliveries carry request_id, status, error, payload and nothing about
cost or which shot they belong to. So at submit time we record
request_id -> shot/estimate in a small SQLite registry; the webhook receiver
joins on request_id when it writes the ledger row.

Charging rule (docs/concepts/billing-and-retention): only `completed` is
charged. `failed` and `nsfw` are refunded; a successful cancel is refunded.
The ledger therefore records estimated cost on every row and a charged_usd
column that is non zero only for completed.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .estimate import Estimate
from .shots import Shot
from .submit import SubmitResult

LEDGER_COLUMNS = [
    "ts_utc", "request_id", "status", "shot_id", "episode", "scene", "tier",
    "model_id", "est_credits", "est_usd", "charged_usd", "outputs", "error",
]

_lock = threading.Lock()


@dataclass(frozen=True)
class RegistryRow:
    request_id: str
    shot_id: str
    episode: str
    scene: str
    tier: str
    model_id: str
    est_credits: str
    est_usd: str
    idempotency_key: str


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS submissions(
                    request_id TEXT PRIMARY KEY, shot_id TEXT, episode TEXT, scene TEXT,
                    tier TEXT, model_id TEXT, est_credits TEXT, est_usd TEXT,
                    idempotency_key TEXT, submitted_utc TEXT)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS deliveries(
                    request_id TEXT, status TEXT, received_utc TEXT,
                    PRIMARY KEY(request_id, status))"""
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None, timeout=10)

    def record_submission(self, res: SubmitResult, shot: Shot, est: Estimate | None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    res.request_id, shot.shot_id, shot.episode, shot.scene, shot.tier,
                    shot.model_id,
                    str(est.credits) if est and est.credits is not None else "", str(est.usd) if est else "",
                    res.idempotency_key, _now(),
                ),
            )

    def lookup(self, request_id: str) -> RegistryRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT request_id, shot_id, episode, scene, tier, model_id, est_credits, est_usd, idempotency_key "
                "FROM submissions WHERE request_id=?", (request_id,)
            ).fetchone()
        return RegistryRow(*row) if row else None

    def mark_delivered(self, request_id: str, status: str) -> bool:
        """Returns True the first time (request_id, status) is seen, False on a duplicate."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?,?,?)", (request_id, status, _now())
            )
            return cur.rowcount == 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_ledger(
    ledger_path: Path,
    *,
    request_id: str,
    status: str,
    reg: RegistryRow | None,
    outputs: list[str],
    error: str | None,
) -> None:
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    est_usd = Decimal(reg.est_usd) if reg and reg.est_usd else Decimal("0")
    charged = est_usd if status == "completed" else Decimal("0")
    row = {
        "ts_utc": _now(),
        "request_id": request_id,
        "status": status,
        "shot_id": reg.shot_id if reg else "",
        "episode": reg.episode if reg else "",
        "scene": reg.scene if reg else "",
        "tier": reg.tier if reg else "",
        "model_id": reg.model_id if reg else "",
        "est_credits": reg.est_credits if reg else "",
        "est_usd": str(est_usd),
        "charged_usd": str(charged),
        "outputs": ";".join(outputs),
        "error": error or "",
    }
    with _lock:
        new = not ledger_path.exists() or ledger_path.stat().st_size == 0
        with ledger_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(row)
