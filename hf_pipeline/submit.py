"""Submit a shot with submit_async semantics plus an Idempotency-Key.

higgsfield-client 0.2.0's AsyncClient.submit (exported module-level as
submit_async) builds the URL with ?hf_webhook=... and POSTs through the SDK
transport, but it has no hook for extra headers. Idempotency-Key is a
documented header (docs/concepts/idempotency), so we subclass the SDK client
and override submit to pass the header through the same transport. The return
value is the SDK's own AsyncRequestController, so status/result/cancel work
exactly as with submit_async.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from higgsfield_client import AsyncClient, SyncClient
from higgsfield_client.http.client import AsyncRequestController, SyncRequestController
from higgsfield_client.utils import get_url

from .config import Settings, load_settings
from .http import classify_http_error
from .shots import Shot, idempotency_key_for


@dataclass(frozen=True)
class SubmitResult:
    shot_id: str
    request_id: str
    status_url: str
    idempotency_key: str
    accepted_status: str      # acceptance receipt; may say "queued" even on a replay
    controller: Any           # AsyncRequestController | SyncRequestController


class IdempotentAsyncClient(AsyncClient):
    async def submit(  # type: ignore[override]
        self,
        application: str,
        arguments: dict[str, Any],
        *,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncRequestController:
        url = get_url(application, webhook_url)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        try:
            response = await self._transport.request(
                "POST", url, json=arguments, timeout=self.timeout, headers=headers
            )
        except Exception as e:  # SDK wraps HTTP errors; reclassify when we can see the response
            cause = e.__cause__
            if isinstance(cause, httpx.HTTPStatusError):
                raise classify_http_error(cause.response) from e
            raise
        data = response.json()
        ctrl = AsyncRequestController(
            request_id=data["request_id"],
            response_url=data["status_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            transport=self._transport,
        )
        ctrl.accepted_status = data.get("status", "")  # type: ignore[attr-defined]
        return ctrl


class IdempotentSyncClient(SyncClient):
    def submit(  # type: ignore[override]
        self,
        application: str,
        arguments: dict[str, Any],
        *,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> SyncRequestController:
        url = get_url(application, webhook_url)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        try:
            response = self._transport.request(
                "POST", url, json=arguments, timeout=self.timeout, headers=headers
            )
        except Exception as e:
            cause = e.__cause__
            if isinstance(cause, httpx.HTTPStatusError):
                raise classify_http_error(cause.response) from e
            raise
        data = response.json()
        ctrl = SyncRequestController(
            request_id=data["request_id"],
            response_url=data["status_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            transport=self._transport,
        )
        ctrl.accepted_status = data.get("status", "")  # type: ignore[attr-defined]
        return ctrl


_async_client: IdempotentAsyncClient | None = None
_sync_client: IdempotentSyncClient | None = None


def _aclient() -> IdempotentAsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = IdempotentAsyncClient()
    return _async_client


def _sclient() -> IdempotentSyncClient:
    global _sync_client
    if _sync_client is None:
        _sync_client = IdempotentSyncClient()
    return _sync_client


async def submit_shot(shot: Shot, *, settings: Settings | None = None, revision: int = 0) -> SubmitResult:
    """Submit one shot asynchronously. Webhook comes from HF_WEBHOOK_URL."""
    settings = settings or load_settings()
    key = idempotency_key_for(shot.shot_id, revision=revision)
    ctrl = await _aclient().submit(
        shot.model_id, shot.body, webhook_url=settings.webhook_url, idempotency_key=key
    )
    return SubmitResult(
        shot_id=shot.shot_id,
        request_id=ctrl.request_id,
        status_url=ctrl.status_url,
        idempotency_key=key,
        accepted_status=getattr(ctrl, "accepted_status", ""),
        controller=ctrl,
    )


def submit_shot_sync(shot: Shot, *, settings: Settings | None = None, revision: int = 0) -> SubmitResult:
    settings = settings or load_settings()
    key = idempotency_key_for(shot.shot_id, revision=revision)
    ctrl = _sclient().submit(
        shot.model_id, shot.body, webhook_url=settings.webhook_url, idempotency_key=key
    )
    return SubmitResult(
        shot_id=shot.shot_id,
        request_id=ctrl.request_id,
        status_url=ctrl.status_url,
        idempotency_key=key,
        accepted_status=getattr(ctrl, "accepted_status", ""),
        controller=ctrl,
    )
