"""Shot definition and idempotency key derivation."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# Stable namespace for this pipeline. Changing it changes every key, which
# would let already submitted shots be resubmitted and billed again.
_NAMESPACE = uuid.UUID("7a3f9e1c-5b2d-4c8e-9f0a-1d2e3c4b5a69")
_KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")  # 1-255 visible ASCII, no whitespace


@dataclass(frozen=True)
class Shot:
    shot_id: str                 # unique within the project, e.g. "ep01_sc03_sh02"
    model_id: str                # path style id, e.g. "bytedance/seedance-2.5/text-to-video"
    body: dict[str, Any]         # exact request body for that model
    tier: str = "standard"
    scene: str = ""
    episode: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def idempotency_key_for(shot_id: str, *, revision: int = 0) -> str:
    """Deterministic UUID derived from the shot id.

    Same shot id => same key => Higgsfield replays the original request_id and
    does not bill again (same endpoint, body, and webhook). If you deliberately
    change a shot's body, bump `revision` so the server does not reject the
    submission with 422 for reusing a key with different parameters.
    """
    if not shot_id or shot_id != shot_id.strip():
        raise ValueError("shot_id must be non empty with no surrounding whitespace")
    material = f"{shot_id}#{revision}" if revision else shot_id
    key = str(uuid.uuid5(_NAMESPACE, material))
    assert _KEY_RE.match(key)
    return key


def body_fingerprint(body: dict[str, Any]) -> str:
    """Stable hash of a body; useful for detecting local drift before the server 422s."""
    import json
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
