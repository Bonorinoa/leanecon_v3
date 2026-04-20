"""Structured logging helpers for observability."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("leanecon.observability")


def log_event(event: str, **payload: Any) -> None:
    message = {"event": event, **payload}
    logger.info(json.dumps(message, sort_keys=True, default=str))

