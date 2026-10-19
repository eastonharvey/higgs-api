"""Seedance 2.5 text-to-video example using the official SDK's subscribe().

Makes ONE billable generation request. Credentials are read by the SDK from
HF_KEY (loaded from .env.local); this script never reads or prints the key.

    python main.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import higgsfield_client
from higgsfield_client import Cancelled, Completed, Failed, NSFW, Status
from higgsfield_client.exceptions import CredentialsMissedError, HiggsfieldClientError

MODEL_ID = "bytedance/seedance-2.5/text-to-video"
ARGUMENTS = {
    "prompt": "A cinematic scene at sunset",
    "duration": 5,
    "resolution": "720p",
    "aspect_ratio": "16:9",
}


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env.local"), override=False)
    if not os.environ.get("HF_KEY"):
        print("HF_KEY is not set. Put HF_KEY=key-id:key-secret in .env.local and rerun.", file=sys.stderr)
        return 2

    last: Status | None = None

    def on_enqueue(request_id: str) -> None:
        print(f"queued request_id={request_id}")

    def on_queue_update(status: Status) -> None:
        nonlocal last
        if type(status) is not type(last):
            print(f"status: {type(status).__name__.lower()}")
        last = status

    try:
        result = higgsfield_client.subscribe(
            MODEL_ID, ARGUMENTS, on_enqueue=on_enqueue, on_queue_update=on_queue_update
        )
    except CredentialsMissedError as e:
        print(f"credentials error: {e}", file=sys.stderr)
        return 2
    except HiggsfieldClientError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1

    # subscribe() returns the final status document. Only `completed` has output.
    status = result.get("status")
    if status == "completed" or isinstance(last, Completed):
        video = result.get("video") or {}
        url = video.get("url") if isinstance(video, dict) else None
        if not url:
            print(f"completed but no video url in response: {result}", file=sys.stderr)
            return 1
        print(f"VIDEO_URL={url}")
        return 0
    if status == "failed" or isinstance(last, Failed):
        print(f"generation failed: {result.get('error') or 'Generation failed'} (not charged)", file=sys.stderr)
        return 1
    if status == "nsfw" or isinstance(last, NSFW):
        print("generation moderated (nsfw); no output, not charged", file=sys.stderr)
        return 1
    if status == "canceled" or isinstance(last, Cancelled):
        print("generation canceled before processing; no output", file=sys.stderr)
        return 1
    print(f"unexpected terminal state: {result}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
