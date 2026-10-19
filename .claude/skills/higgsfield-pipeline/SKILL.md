---
name: higgsfield-pipeline
description: Operate the higgs-api repo (hf_pipeline) against the Higgsfield API: setup, estimates and budget gate, idempotent submits, webhook receiver, cost ledger, and known API quirks for Seedance 2.5, Kling 3.0 4K, Wan 3.0, Soul v2.
---

# Higgsfield pipeline (higgs-api / hf_pipeline)

Python package `hf_pipeline` on `higgsfield-client` 0.2.0. Credentials only from `HF_KEY` in `.env.local` (gitignored). Never read, print, log, paste, or commit the key. If the key is missing, ask the user to add `HF_KEY=key-id:key-secret` to `.env.local` locally; never ask for it in chat.

## Before touching the API
1. Read the model's api-reference at `https://open.higgsfield.ai/models/<model_id>/api-reference` (console.higgsfield.ai redirects there). Shared docs: `https://docs.higgsfield.ai/docs/llms.txt`.
2. Do not substitute models or endpoints. If a page is unreachable, say which and stop.
3. Every generation is billable. Estimate first, state the number, get a yes.

## Model ids in models.json
| tier | model_id | notes |
|---|---|---|
| video.draft | bytedance/seedance-2.5/text-to-video | 480p, no audio |
| video.standard | bytedance/seedance-2.5/text-to-video | 720p, audio. duration 4-30; res 480p/720p/1080p; ar 16:9,4:3,1:1,3:4,9:16,21:9 |
| video.hero | kling-video/v3.0/4k/text-to-video | duration 3-15; ar 16:9,9:16,1:1; cfg_scale 0-1; sound on/off |
| video.animate | alibaba/wan-3.0/image-to-video | needs prompt + image_url; duration 2-30; ar adds adaptive |
| image.sheet | higgsfield-ai/soul/v2/standard | batch_size 1 or 4; res 720p/1080p; ar 9:16,16:9,4:3,3:4,1:1,2:3,3:2 |

Nano Banana Pro has no public path style api-reference; do not add it unless the user supplies the URL.

## Setup (new machine or new clone)
```bash
python3 -m venv .venv && source .venv/bin/activate   # every new terminal needs the source line
pip install -r requirements.txt
cp .env.example .env.local   # user adds HF_KEY locally
python -m pytest -q          # offline, mocked HTTP; expect all green
```
Run commands bare. A trailing `# comment` pasted into zsh becomes an argument and breaks pytest.

## Daily commands
```bash
python smoke_test.py                                  # two estimates, zero generations
python main.py                                        # ONE billable Seedance 2.5 shot, prints VIDEO_URL=
uvicorn hf_pipeline.webhook:app --host 0.0.0.0 --port 8080   # receiver; expose via HTTPS tunnel, set HF_WEBHOOK_URL
python -m hf_pipeline.run shots.json --ceiling 5.00   # estimate all -> abort if over -> pooled idempotent submit
python -m hf_pipeline.cost_report                     # per scene / episode / tier
```
Env: `HF_KEY` (required), `HF_WEBHOOK_URL` (https, path /webhooks/higgsfield), `HF_CONCURRENCY` (default 2, keep at or under console limit), `OUTPUT_DIR` (default ./output).

## Package map
- `estimate.py`: `estimate(model_id, body)`, `budget_gate(shots, ceiling_usd)`. Gate estimates every shot before any submit; raises `BudgetExceeded`.
- `shots.py`: `Shot`, `idempotency_key_for(shot_id, revision=0)` = uuid5. Same key + same body replays the original request_id with no second charge; changed body with same key gets 422, so bump `revision` when a shot is deliberately changed.
- `submit.py`: `submit_shot(shot)` subclasses the SDK AsyncClient to add the `Idempotency-Key` header and `?hf_webhook=` through the SDK transport. Returns the SDK's own controller.
- `pool.py`: `run_pool` with `asyncio.Semaphore(HF_CONCURRENCY)`; on HTTP 400 "Maximum number of concurrent requests (N) has been reached" retries with exponential backoff and full jitter.
- `webhook.py`: strict envelope `{request_id uuid, status completed|failed|nsfw, error, payload}` (extra keys -> 422); dedupe on request_id+status (duplicates -> 2xx); downloads `payload.video` / `payload.images[]` to OUTPUT_DIR; appends `cost_ledger.csv`. Must answer within 10 s; 4xx is permanent, 5xx retried up to 2 h.
- `ledger.py`: sqlite registry (request_id -> shot, tier, scene, episode, estimate) written at submit time; webhook joins on request_id. `charged_usd` is non zero only for `completed`.
- `cost_report.py`: reads the ledger CSV.

## Known API facts and quirks (verified 2026-10-18)
- SDK 0.2.0 has no `estimate()` and no idempotency hook. Both go through the SDK's authenticated httpx client (`hf_pipeline/http.py`). Module API: `subscribe`, `submit_async(app, args, webhook_url=)`, `status/result/cancel(request_id)`. Statuses: queued, in_progress, completed, failed, nsfw, canceled.
- Estimate endpoint `POST /estimate/{model_id}`. Docs say `{credits, usd}`. Seedance 2.5 actually returns `{type: "description", pricing_description: "...$0.2056 per second at 480p, $0.4622 at 720p, $1.1372 at 1080p..."}`. `estimate.py` parses the per second sentence and marks the Estimate `approximate=True`. 5 s at 720p is about $2.31. Reconcile first ledger rows against the console.
- Billing: only `completed` is charged; failed/nsfw refunded; cancel only while queued. Outputs retained about 7 days, so the receiver downloads them.
- Errors: 400 bad params or concurrency; 401 creds; 403 `not_enough_credits` (account balance is zero, user must load credits in the console, no code fix); 422 validation or idempotency conflict; 423/503 model unavailable, retry later; 500 retry with backoff. No Retry-After header.
- Webhook payload carries no cost and no shot id; that is why the registry exists.

## Environment gotchas
- Apple CLT Python is 3.9. `webhook.py` uses `Optional[...]` typing for pydantic; keep it that way or require 3.10+. Prefer `brew install python@3.12` and rebuild the venv.
- Cloud sandboxes may not reach api.higgsfield.ai. Billable verification runs on a real machine; never claim success without a pasted `VIDEO_URL=`.

## Done criteria for "it works"
`python -m pytest -q` green, `python smoke_test.py` prints two estimates, `python main.py` prints `VIDEO_URL=https://...` after a `completed` status. Anything else is not working, say so.
