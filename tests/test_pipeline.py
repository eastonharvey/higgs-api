import asyncio
import csv
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

os.environ.setdefault("HF_KEY", "test-id:test-secret")  # dummy; never a real key

from hf_pipeline import budget_gate, estimate, idempotency_key_for, load_models, resolve_shot
from hf_pipeline.estimate import BudgetExceeded
from hf_pipeline.http import ConcurrencyLimitError, IdempotencyConflictError
from hf_pipeline.pool import run_pool
from hf_pipeline.shots import Shot
from hf_pipeline.submit import submit_shot
from hf_pipeline.config import Settings
from hf_pipeline.ledger import Registry, append_ledger
from hf_pipeline import cost_report

BASE = "https://api.higgsfield.ai"


def _settings(tmp_path: Path, webhook="https://hooks.example.org/webhooks/higgsfield", conc=2) -> Settings:
    return Settings(webhook_url=webhook, concurrency=conc, output_dir=tmp_path / "out",
                    ledger_path=tmp_path / "out" / "cost_ledger.csv", models_path=Path("hf_pipeline/models.json"))


# ---------- models.json ----------

def test_models_json_exact_ids():
    m = load_models()
    assert m["video"]["draft"].model_id == "bytedance/seedance-2.5/text-to-video"
    assert m["video"]["standard"].model_id == "bytedance/seedance-2.5/text-to-video"
    assert m["video"]["hero"].model_id == "kling-video/v3.0/4k/text-to-video"
    assert m["video"]["animate"].model_id == "alibaba/wan-3.0/image-to-video"
    assert m["image"]["sheet"].model_id == "higgsfield-ai/soul/v2/standard"
    s = resolve_shot("x", "p", kind="video", tier="standard", duration=5, resolution="720p", aspect_ratio="16:9")
    assert s.body["prompt"] == "p" and s.body["duration"] == 5 and s.body["resolution"] == "720p"


# ---------- idempotency ----------

def test_idempotency_key_deterministic_and_valid():
    a, b = idempotency_key_for("ep01_sc01_sh01"), idempotency_key_for("ep01_sc01_sh01")
    assert a == b and uuid.UUID(a)
    assert idempotency_key_for("ep01_sc01_sh01", revision=1) != a
    assert idempotency_key_for("other") != a
    with pytest.raises(ValueError):
        idempotency_key_for(" bad ")


# ---------- estimate + budget gate ----------

@respx.mock
def test_estimate_and_budget_gate():
    respx.post(f"{BASE}/estimate/bytedance/seedance-2.5/text-to-video").mock(
        return_value=httpx.Response(200, json={"credits": "7.000", "usd": "0.420"}))
    respx.post(f"{BASE}/estimate/higgsfield-ai/soul/v2/standard").mock(
        return_value=httpx.Response(200, json={"credits": "1.500", "usd": "0.094"}))
    e = estimate("bytedance/seedance-2.5/text-to-video", {"prompt": "x"})
    assert e.usd == Decimal("0.420") and e.credits == Decimal("7.000")

    shots = [
        resolve_shot("a", "x", kind="video", tier="standard"),
        resolve_shot("b", "x", kind="image", tier="sheet"),
    ]
    lines = budget_gate(shots, "1.00")
    assert [l.shot_id for l in lines] == ["a", "b"]
    assert sum(l.usd for l in lines) == Decimal("0.514")
    with pytest.raises(BudgetExceeded) as ei:
        budget_gate(shots, "0.50")
    assert ei.value.total_usd == Decimal("0.514")


# ---------- submit with Idempotency-Key + hf_webhook ----------

@respx.mock
def test_submit_sets_header_and_webhook(tmp_path):
    rid = str(uuid.uuid4())
    route = respx.post(f"{BASE}/bytedance/seedance-2.5/text-to-video").mock(
        return_value=httpx.Response(200, json={
            "request_id": rid, "status": "queued",
            "status_url": f"{BASE}/requests/{rid}/status", "cancel_url": f"{BASE}/requests/{rid}/cancel"}))
    shot = resolve_shot("ep01_sc01_sh01", "A cinematic scene at sunset", kind="video", tier="standard")
    res = asyncio.run(submit_shot(shot, settings=_settings(tmp_path)))
    req = route.calls.last.request
    assert req.headers["Idempotency-Key"] == idempotency_key_for("ep01_sc01_sh01")
    assert req.headers["Authorization"].startswith("Key ")
    assert "hf_webhook=https%3A%2F%2Fhooks.example.org%2Fwebhooks%2Fhiggsfield" in str(req.url)
    assert json.loads(req.content)["prompt"] == "A cinematic scene at sunset"
    assert res.request_id == rid and res.accepted_status == "queued"


@respx.mock
def test_submit_idempotency_conflict(tmp_path):
    respx.post(f"{BASE}/higgsfield-ai/soul/v2/standard").mock(
        return_value=httpx.Response(422, json={"detail": "Idempotency-Key was already used with different request parameters"}))
    shot = resolve_shot("s1", "p", kind="image", tier="sheet")
    with pytest.raises(IdempotencyConflictError):
        asyncio.run(submit_shot(shot, settings=_settings(tmp_path)))


# ---------- pool: backoff on 400 concurrency ----------

def test_pool_backs_off_on_concurrency_error(tmp_path, monkeypatch):
    import hf_pipeline.pool as pool
    monkeypatch.setattr(pool.random, "uniform", lambda a, b: 0.0)  # no real sleeping
    calls = {"n": 0}

    async def fake_submit(shot, settings=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConcurrencyLimitError("Maximum number of concurrent requests (4) has been reached")
        from hf_pipeline.submit import SubmitResult
        return SubmitResult(shot.shot_id, "rid", "u", "k", "queued", None)

    shots = [Shot("a", "bytedance/seedance-2.5/text-to-video", {"prompt": "x"})]
    rep = asyncio.run(run_pool(shots, settings=_settings(tmp_path), submit=fake_submit, base_delay=0, max_delay=0))
    assert calls["n"] == 3 and len(rep.submitted) == 1 and not rep.failed


def test_pool_respects_semaphore(tmp_path):
    active = {"now": 0, "peak": 0}

    async def fake_submit(shot, settings=None):
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1
        from hf_pipeline.submit import SubmitResult
        return SubmitResult(shot.shot_id, "rid-" + shot.shot_id, "u", "k", "queued", None)

    shots = [Shot(f"s{i}", "higgsfield-ai/soul/v2/standard", {"prompt": "x"}) for i in range(10)]
    rep = asyncio.run(run_pool(shots, settings=_settings(tmp_path, conc=3), submit=fake_submit))
    assert active["peak"] <= 3 and len(rep.submitted) == 10


# ---------- webhook receiver ----------

@respx.mock
def test_webhook_validates_dedupes_downloads_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("LEDGER_PATH", raising=False)
    from hf_pipeline import webhook as wh
    app = wh.create_app()
    client = TestClient(app)
    reg: Registry = app.state.registry
    rid = str(uuid.uuid4())

    # register the submission the way run.py does
    from hf_pipeline.submit import SubmitResult
    from hf_pipeline.estimate import Estimate
    shot = Shot("ep01_sc01_sh01", "bytedance/seedance-2.5/text-to-video", {"prompt": "x"},
                tier="standard", scene="sc01", episode="ep01")
    reg.record_submission(SubmitResult("ep01_sc01_sh01", rid, "u", "k", "queued", None), shot,
                          Estimate(shot.model_id, Decimal("7.000"), Decimal("0.420")))

    respx.get("https://cdn.example.org/out.mp4").mock(return_value=httpx.Response(200, content=b"\x00\x01video"))

    # bad envelope -> 4xx (permanent)
    assert client.post("/webhooks/higgsfield", json={"request_id": "nope", "status": "completed"}).status_code == 422
    assert client.post("/webhooks/higgsfield", json={"request_id": rid, "status": "weird", "error": None, "payload": None}).status_code == 422
    assert client.post("/webhooks/higgsfield", json={"request_id": rid, "status": "completed", "extra": 1}).status_code == 422

    good = {"request_id": rid, "status": "completed", "error": None,
            "payload": {"video": {"url": "https://cdn.example.org/out.mp4", "content_type": "video/mp4"}}}
    r = client.post("/webhooks/higgsfield", json=good)
    assert r.status_code == 200 and r.json()["ok"] == "accepted"
    # duplicate -> 2xx, not processed again
    r2 = client.post("/webhooks/higgsfield", json=good)
    assert r2.status_code == 200 and r2.json()["ok"] == "duplicate"

    # failed status for a different request: ledger row with charged 0
    rid2 = str(uuid.uuid4())
    client.post("/webhooks/higgsfield", json={"request_id": rid2, "status": "failed", "error": "Generation failed", "payload": None})

    files = list((tmp_path / "out").glob("ep01_sc01_sh01_*.mp4"))
    assert len(files) == 1 and files[0].read_bytes() == b"\x00\x01video"

    rows = list(csv.DictReader((tmp_path / "out" / "cost_ledger.csv").open()))
    assert len(rows) == 2
    done = next(r for r in rows if r["request_id"] == rid)
    assert done["charged_usd"] == "0.420" and done["scene"] == "sc01" and done["tier"] == "standard"
    failed = next(r for r in rows if r["request_id"] == rid2)
    assert failed["charged_usd"] == "0" and failed["error"] == "Generation failed"


def test_cost_report(tmp_path, capsys):
    led = tmp_path / "cost_ledger.csv"
    from hf_pipeline.ledger import RegistryRow
    for ep, sc, tier, st, usd in [("ep01", "sc01", "draft", "completed", "0.10"), ("ep01", "sc02", "hero", "completed", "1.50"),
                                  ("ep02", "sc01", "standard", "failed", "0.42"), ("ep02", "cast", "sheet", "completed", "0.094")]:
        append_ledger(led, request_id=str(uuid.uuid4()), status=st,
                      reg=RegistryRow("r", "s", ep, sc, tier, "m", "1", usd, "k"), outputs=[], error=None)
    assert cost_report.main([str(led)]) == 0
    out = capsys.readouterr().out
    assert "Per scene" in out and "Per episode" in out and "Per tier" in out
    assert "1.694" in out  # total charged = 0.10 + 1.50 + 0.094


SEEDANCE_DESC = {"type": "description", "pricing_description": "For 16:9 video without video input, your request costs roughly $0.2056 per second of generated video at 480p, $0.4622 at 720p, and $1.1372 at 1080p. Each 1,000 video tokens costs $0.0214 at 480p or 720p and $0.0234 at 1080p. Billable video tokens = ceil(output height x output width x (input video duration + generated video duration) x 24 / 1024). Image and audio references do not count as video input. Actual pricing depends on output dimensions and billable duration. Rates shown are before any applicable customer discount."}


@respx.mock
def test_estimate_description_shape_seedance():
    respx.post(f"{BASE}/estimate/bytedance/seedance-2.5/text-to-video").mock(
        return_value=httpx.Response(200, json=SEEDANCE_DESC))
    e = estimate("bytedance/seedance-2.5/text-to-video", {"prompt": "x", "duration": 5, "resolution": "720p"})
    assert e.approximate and e.credits is None and e.usd == Decimal("2.3110")
    e2 = estimate("bytedance/seedance-2.5/text-to-video", {"prompt": "x", "duration": 10, "resolution": "480p"})
    assert e2.usd == Decimal("2.0560")
    from hf_pipeline.estimate import EstimateUnavailable
    with pytest.raises(EstimateUnavailable):
        estimate("bytedance/seedance-2.5/text-to-video", {"prompt": "x", "duration": 5, "resolution": "4k"})
