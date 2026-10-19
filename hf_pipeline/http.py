"""Authenticated HTTP access for endpoints the SDK does not wrap.

higgsfield-client 0.2.0 exposes subscribe/submit/status/result/cancel but has
no estimate() and no Idempotency-Key support. Both are documented REST features
(docs/concepts/billing-and-retention, docs/concepts/idempotency), so we call them
through the SDK's own authenticated httpx client. The Authorization header is
built by the SDK from HF_KEY; this module never touches the key value.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from higgsfield_client import AsyncClient, SyncClient
from higgsfield_client.exceptions import HiggsfieldClientError

_CONCURRENCY_RE = re.compile(r"maximum number of concurrent requests", re.IGNORECASE)


class ConcurrencyLimitError(Exception):
    """HTTP 400 'Maximum number of concurrent requests (N) has been reached'."""


class IdempotencyConflictError(Exception):
    """HTTP 422: Idempotency-Key reused with different request parameters."""


class InsufficientCreditsError(Exception):
    """HTTP 403: account lacks credits."""


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        d = data.get("detail") if isinstance(data, dict) else None
        if isinstance(d, list):
            return "; ".join(str(x) for x in d)
        return str(d or data)
    except ValueError:
        return resp.text


def classify_http_error(resp: httpx.Response) -> Exception:
    detail = _error_detail(resp)
    code = resp.status_code
    if code == 400 and _CONCURRENCY_RE.search(detail):
        return ConcurrencyLimitError(detail)
    if code == 422 and "idempotency" in detail.lower():
        return IdempotencyConflictError(detail)
    if code == 403:
        return InsufficientCreditsError(detail)
    corr = resp.headers.get("X-Correlation-ID", "")
    suffix = f" (X-Correlation-ID={corr})" if corr else ""
    return HiggsfieldClientError(f"HTTP {code}: {detail}{suffix}")


def is_concurrency_error(exc: BaseException) -> bool:
    if isinstance(exc, ConcurrencyLimitError):
        return True
    return isinstance(exc, HiggsfieldClientError) and bool(_CONCURRENCY_RE.search(str(exc)))


# ---- clients -----------------------------------------------------------------

_sync: SyncClient | None = None
_async: AsyncClient | None = None


def sync_client() -> SyncClient:
    global _sync
    if _sync is None:
        _sync = SyncClient()
    return _sync


def async_client() -> AsyncClient:
    global _async
    if _async is None:
        _async = AsyncClient()
    return _async


def raw_sync() -> httpx.Client:
    """SDK-managed httpx client with Authorization already set."""
    return sync_client()._client  # noqa: SLF001  (stable in 0.2.0; auth stays in the SDK)


def raw_async() -> httpx.AsyncClient:
    return async_client()._client  # noqa: SLF001


def post_json_sync(path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None,
                   params: dict[str, str] | None = None) -> dict[str, Any]:
    resp = raw_sync().post(path, json=body, headers=headers, params=params)
    if resp.is_error:
        raise classify_http_error(resp)
    return resp.json()


async def post_json_async(path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None,
                          params: dict[str, str] | None = None) -> dict[str, Any]:
    resp = await raw_async().post(path, json=body, headers=headers, params=params)
    if resp.is_error:
        raise classify_http_error(resp)
    return resp.json()
