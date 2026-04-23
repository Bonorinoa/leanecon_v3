"""Shared progress-event formatting used by the API and benchmark harness."""

from __future__ import annotations

from typing import Any

from .telemetry import utc_now


def build_progress_event(
    event: str,
    *,
    job_id: str | None = None,
    claim_id: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    review_state: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event,
        "timestamp": utc_now(),
        "job_id": job_id,
        "claim_id": claim_id,
        "stage": stage,
        "status": status,
        "review_state": review_state,
        "message": message,
        "metadata": dict(metadata or {}),
    }
    return payload
