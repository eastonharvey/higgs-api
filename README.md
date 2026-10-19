# higgs: Higgsfield API pipeline (Seedance 2.5, Kling 3.0 4K, Wan 3.0, Soul 2)

Python 3.9+, `higgsfield-client` 0.2.0. Credentials only from `HF_KEY` in `.env.local` (gitignored).

## Setup

```bash
cd ~/higgs
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local      # then put HF_KEY=key-id:key-secret in .env.local
```

## Verify (step 1: free, step 2: billable)

```bash
python smoke_test.py   # two POST /estimate calls (Seedance 2.5 720p 5s + Soul v2). Submits nothing.
python main.py         # ONE billable Seedance 2.5 generation via SDK subscribe(); prints VIDEO_URL=...
python -m pytest -q    # offline unit tests, mocked HTTP
```

`main.py` exits 0 only on `completed` with a `video.url`. `failed`, `nsfw`, `canceled` exit 1 with the reason and are not charged.

## Pipeline

```bash
# terminal 1: webhook receiver (expose over HTTPS; set HF_WEBHOOK_URL to https://<host>/webhooks/higgsfield)
uvicorn hf_pipeline.webhook:app --host 0.0.0.0 --port 8080

# terminal 2: estimate everything, abort if over ceiling, else submit through the worker pool
python -m hf_pipeline.run shots.example.json --ceiling 5.00

# later
python -m hf_pipeline.cost_report            # per scene, per episode, per tier
```

| Piece | Where | Behavior |
|---|---|---|
| `estimate(model_id, body)` | `hf_pipeline/estimate.py` | `POST /estimate/{model_id}` → `credits`, `usd` as `Decimal` |
| `budget_gate(shots, ceiling_usd)` | same | estimates every shot first; raises `BudgetExceeded` before any submission |
| `submit_shot(shot)` | `hf_pipeline/submit.py` | SDK `submit_async` semantics + `Idempotency-Key` = uuid5(shot_id) + `?hf_webhook=` from `HF_WEBHOOK_URL` |
| worker pool | `hf_pipeline/pool.py` | `asyncio.Semaphore(HF_CONCURRENCY)`; on HTTP 400 "Maximum number of concurrent requests" retries with exponential backoff + full jitter |
| webhook | `hf_pipeline/webhook.py` | strict envelope (`request_id` uuid, `status` ∈ completed/failed/nsfw, `error`, `payload`, no extras) → 422 on mismatch; dedupe on `request_id+status` → 2xx on duplicates; downloads `payload.video` / `payload.images[]` to `OUTPUT_DIR`; appends `cost_ledger.csv` |
| tiers | `hf_pipeline/models.json` | video `draft`/`standard` = Seedance 2.5, `hero` = Kling 3.0 4K, `animate` = Wan 3.0 I2V; image `sheet` = Soul v2 |
| `cost_report.py` | `hf_pipeline/cost_report.py` | reads ledger; `charged_usd` only counts `completed` |

## Facts the code relies on (from docs read 2026-10-18)

- Billing: only `completed` is charged; `failed`/`nsfw` refunded; cancel only works while `queued`. Outputs retained at least 7 days, so the receiver downloads them.
- Idempotency: header `Idempotency-Key`, 1–255 visible ASCII. Same key + same body + same webhook replays the original `request_id` (no second charge). Different body → 422. Bump `revision=` in `idempotency_key_for` when you deliberately change a shot.
- Rate limits: concurrency per account and per model; error is 400 `{"detail": "Maximum number of concurrent requests (N) has been reached"}`, no `Retry-After`.
- Webhooks: HTTPS only, must answer within 10 s, 4xx is permanent, 5xx retried up to 2 h, duplicates possible.
- SDK 0.2.0 has no `estimate()` and no idempotency hook. Both are done through the SDK's own authenticated httpx client (`hf_pipeline/http.py`, `submit.py`), so the key is handled only by the SDK.

## Not included

- Nano Banana Pro: no public path style api-reference page exists on open.higgsfield.ai (sitemap and OpenAPI checked). The image `sheet` tier uses Soul v2 by decision, not substitution.
