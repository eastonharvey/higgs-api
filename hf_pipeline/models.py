"""Tier registry backed by models.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .shots import Shot

_DEFAULT_PATH = Path(__file__).with_name("models.json")


@dataclass(frozen=True)
class ModelTier:
    kind: str          # "video" | "image"
    tier: str          # "draft" | "standard" | "hero" | "animate" | "sheet"
    model_id: str
    params: dict[str, Any]
    notes: str = ""

    def body(self, prompt: str, **overrides: Any) -> dict[str, Any]:
        b = {"prompt": prompt, **self.params, **overrides}
        return b


def load_models(path: Path | str | None = None) -> dict[str, dict[str, ModelTier]]:
    p = Path(path) if path else _DEFAULT_PATH
    raw = json.loads(p.read_text())
    out: dict[str, dict[str, ModelTier]] = {}
    for kind, tiers in raw.items():
        if kind.startswith("_"):
            continue
        out[kind] = {}
        for tier, spec in tiers.items():
            mid = spec["model_id"]
            if not isinstance(mid, str) or mid.count("/") < 2 or mid.startswith("/"):
                raise ValueError(f"models.json {kind}.{tier}: model_id must be path style, got {mid!r}")
            out[kind][tier] = ModelTier(kind, tier, mid, dict(spec.get("params", {})), spec.get("notes", ""))
    return out


def resolve_shot(
    shot_id: str,
    prompt: str,
    *,
    kind: str = "video",
    tier: str = "standard",
    scene: str = "",
    episode: str = "",
    models: dict[str, dict[str, ModelTier]] | None = None,
    **overrides: Any,
) -> Shot:
    models = models or load_models()
    try:
        mt = models[kind][tier]
    except KeyError as e:
        raise KeyError(f"No tier {kind}.{tier} in models.json") from e
    return Shot(
        shot_id=shot_id,
        model_id=mt.model_id,
        body=mt.body(prompt, **overrides),
        tier=tier,
        scene=scene,
        episode=episode,
        meta={"kind": kind},
    )
