"""Runtime configuration. Loads .env.local then reads env vars.

HF_KEY is required by the SDK but is never read into Python by this module;
the SDK reads it from the process environment itself. We only check presence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_URL = "https://api.higgsfield.ai"


@dataclass(frozen=True)
class Settings:
    webhook_url: str | None
    concurrency: int
    output_dir: Path
    ledger_path: Path
    models_path: Path
    base_url: str = BASE_URL


def _load_env_files() -> None:
    # .env.local wins over .env; never override values already in the environment.
    here = Path.cwd()
    for name in (".env", ".env.local"):
        p = here / name
        if p.exists():
            load_dotenv(p, override=False)


def require_credentials() -> None:
    """Fail fast if HF_KEY is absent. Does not return or log the value."""
    if not os.environ.get("HF_KEY"):
        raise RuntimeError(
            "HF_KEY is not set. Add HF_KEY=key-id:key-secret to .env.local "
            "(the file is gitignored) and rerun."
        )


def load_settings(*, need_credentials: bool = True) -> Settings:
    _load_env_files()
    if need_credentials:
        require_credentials()

    output_dir = Path(os.environ.get("OUTPUT_DIR", "./output")).resolve()
    ledger = Path(os.environ.get("LEDGER_PATH", str(output_dir / "cost_ledger.csv"))).resolve()
    models = Path(os.environ.get("MODELS_PATH", Path(__file__).with_name("models.json"))).resolve()

    try:
        concurrency = int(os.environ.get("HF_CONCURRENCY", "2"))
    except ValueError as e:
        raise RuntimeError("HF_CONCURRENCY must be an integer") from e
    if concurrency < 1:
        raise RuntimeError("HF_CONCURRENCY must be >= 1")

    webhook = os.environ.get("HF_WEBHOOK_URL") or None
    if webhook and not webhook.lower().startswith("https://"):
        raise RuntimeError("HF_WEBHOOK_URL must be an https:// URL (Higgsfield requires HTTPS).")

    return Settings(
        webhook_url=webhook,
        concurrency=concurrency,
        output_dir=output_dir,
        ledger_path=ledger,
        models_path=models,
    )
