"""Semaphore worker pool for submissions.

Concurrency is the number of requests queued or processing at once (per account
and per model). The API returns HTTP 400 with
{"detail": "Maximum number of concurrent requests (N) has been reached"} and no
Retry-After header, so we back off with exponential delay plus full jitter and
retry the same idempotent submission. Polling traffic is separate from
submission concurrency: we only hold the semaphore while submitting.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable

from .config import Settings, load_settings
from .http import is_concurrency_error
from .shots import Shot
from .submit import SubmitResult, submit_shot

log = logging.getLogger("hf_pipeline.pool")


@dataclass
class PoolReport:
    submitted: list[SubmitResult] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # shot_id -> error text


async def _backoff_sleep(attempt: int, base: float, cap: float) -> None:
    # exponential with full jitter: sleep in [0, min(cap, base * 2**attempt)]
    upper = min(cap, base * (2 ** attempt))
    await asyncio.sleep(random.uniform(0, upper))


async def run_pool(
    shots: Iterable[Shot],
    *,
    settings: Settings | None = None,
    concurrency: int | None = None,
    max_attempts: int = 8,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    on_submitted: Callable[[SubmitResult], Awaitable[None] | None] | None = None,
    submit: Callable[..., Awaitable[SubmitResult]] = submit_shot,
) -> PoolReport:
    settings = settings or load_settings()
    limit = concurrency or settings.concurrency
    sem = asyncio.Semaphore(limit)
    report = PoolReport()

    async def worker(shot: Shot) -> None:
        async with sem:
            for attempt in range(max_attempts):
                try:
                    res = await submit(shot, settings=settings)
                except Exception as e:  # noqa: BLE001
                    if is_concurrency_error(e) and attempt < max_attempts - 1:
                        log.warning("concurrency limit on %s (attempt %d); backing off", shot.shot_id, attempt + 1)
                        await _backoff_sleep(attempt, base_delay, max_delay)
                        continue
                    report.failed[shot.shot_id] = f"{type(e).__name__}: {e}"
                    return
                report.submitted.append(res)
                if on_submitted:
                    r = on_submitted(res)
                    if asyncio.iscoroutine(r):
                        await r
                return

    await asyncio.gather(*(worker(s) for s in shots))
    return report
