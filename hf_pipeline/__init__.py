"""hf_pipeline: budgeted, idempotent, webhook-driven Higgsfield generation pipeline.

Credentials come only from HF_KEY (key-id:key-secret). Nothing in this package
reads, prints, or logs the key value.
"""

from .config import Settings, load_settings
from .estimate import Estimate, budget_gate, estimate
from .models import ModelTier, load_models, resolve_shot
from .shots import Shot, idempotency_key_for
from .submit import SubmitResult, submit_shot, submit_shot_sync
from .pool import run_pool

__all__ = [
    "Settings",
    "load_settings",
    "Estimate",
    "estimate",
    "budget_gate",
    "ModelTier",
    "load_models",
    "resolve_shot",
    "Shot",
    "idempotency_key_for",
    "SubmitResult",
    "submit_shot",
    "submit_shot_sync",
    "run_pool",
]
