"""FastAPI webhook receiver.

Contract (docs/how-to/webhooks):
  envelope: {request_id: uuid, status: completed|failed|nsfw, error: str|null, payload: obj|null}
  payload.video {url, content_type} | payload.images [{url, content_type}] | payload.audio
  respond 2xx within 10 s after durably recording; 4xx is permanent (no retry);
  5xx/network retried up to 2 h; duplicates possible -> dedupe on request_id+status.

Run:  uvicorn hf_pipeline.webhook:app --host 0.0.0.0 --port 8080
Expose over HTTPS (reverse proxy or tunnel) and set HF_WEBHOOK_URL to that URL.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from .config import load_settings
from .ledger import Registry, append_ledger

log = logging.getLogger("hf_pipeline.webhook")

TERMINAL = {"completed", "failed", "nsfw"}
_MEDIA_KEYS = ("video", "audio", "zip", "mov", "jsx", "fbx", "ply")
_EXT_BY_TYPE = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "image/png": ".png", "image/jpeg": ".jpg",
    "image/webp": ".webp", "audio/mpeg": ".mp3", "audio/wav": ".wav", "application/zip": ".zip",
}


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    status: str
    error: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    @field_validator("request_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        UUID(v)  # raises ValueError on garbage
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in TERMINAL:
            raise ValueError(f"status must be one of {sorted(TERMINAL)}")
        return v


def extract_outputs(payload: Optional[Dict[str, Any]]) -> List[Tuple[str, Optional[str]]]:
    """-> [(url, content_type)] for every media object in the payload."""
    if not payload:
        return []
    out: List[Tuple[str, Optional[str]]] = []
    for img in payload.get("images") or []:
        if isinstance(img, dict) and img.get("url"):
            out.append((img["url"], img.get("content_type")))
    for key in _MEDIA_KEYS:
        obj = payload.get(key)
        if isinstance(obj, dict) and obj.get("url"):
            out.append((obj["url"], obj.get("content_type")))
    return out


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80] or "x"


def download_outputs(request_id: str, shot_id: Optional[str], outputs: List[Tuple[str, Optional[str]]],
                     output_dir: Path) -> List[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: List[str] = []
    stem = _safe(shot_id or request_id)
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        for i, (url, ctype) in enumerate(outputs):
            ext = _EXT_BY_TYPE.get((ctype or "").split(";")[0].strip().lower())
            if not ext:
                m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", url)
                ext = f".{m.group(1).lower()}" if m else ".bin"
            dest = output_dir / f"{stem}_{request_id[:8]}_{i}{ext}"
            with c.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
            saved.append(str(dest))
    return saved


def create_app() -> FastAPI:
    settings = load_settings(need_credentials=False)
    registry = Registry(settings.output_dir / "registry.sqlite")
    app = FastAPI(title="hf_pipeline webhook")
    app.state.settings = settings
    app.state.registry = registry

    def finish(env: Envelope) -> None:
        reg = registry.lookup(env.request_id)
        outputs = extract_outputs(env.payload) if env.status == "completed" else []
        saved: List[str] = []
        err = env.error
        if outputs:
            try:
                saved = download_outputs(env.request_id, reg.shot_id if reg else None, outputs, settings.output_dir)
            except Exception as e:  # noqa: BLE001
                log.exception("download failed for %s", env.request_id)
                err = f"{err + '; ' if err else ''}download_failed: {e}"
                saved = [u for u, _ in outputs]  # keep URLs so nothing is lost; retention is ~7 days
        append_ledger(settings.ledger_path, request_id=env.request_id, status=env.status,
                      reg=reg, outputs=saved, error=err)
        log.info("ledger: %s %s shot=%s outputs=%d", env.request_id, env.status,
                 reg.shot_id if reg else "?", len(saved))

    @app.post("/webhooks/higgsfield")
    async def receive(request: Request, bg: BackgroundTasks) -> Dict[str, str]:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "body is not JSON")
        try:
            env = Envelope.model_validate(body)
        except Exception as e:  # pydantic ValidationError
            # 4xx = permanent reject; a malformed envelope will not get better on retry
            raise HTTPException(422, f"envelope does not match documented shape: {e}") from e

        first = registry.mark_delivered(env.request_id, env.status)
        if not first:
            return {"ok": "duplicate"}  # 2xx so the sender stops retrying
        # record is durable (sqlite insert); do the slow work after we answer within 10 s
        bg.add_task(finish, env)
        return {"ok": "accepted"}

    @app.get("/healthz")
    async def healthz() -> Dict[str, str]:
        return {"ok": "true"}

    return app


app = create_app()
