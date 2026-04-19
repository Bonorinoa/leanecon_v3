"""SSE formatting helpers."""

from __future__ import annotations

import json
from typing import Any


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"
