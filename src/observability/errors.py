"""Normalized stage error codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib import error as urllib_error

from src.observability.models import AuditEvent, TokenUsage


def classify_exception(exc: BaseException) -> str:
    message = str(exc).lower()
    if isinstance(exc, urllib_error.HTTPError):
        if exc.code in {401, 403}:
            return "auth"
        if exc.code == 429:
            return "rate_limit"
        return "provider_http_error"
    if isinstance(exc, urllib_error.URLError):
        return "provider_unavailable"
    if "timeout" in message:
        return "timeout"
    if "schema" in message or "json" in message:
        return "schema_invalid"
    if "provider" in message:
        return "provider_unavailable"
    if "lsp" in message:
        return "lsp_unavailable"
    return "unknown"


@dataclass
class StageExecutionError(RuntimeError):
    stage: str
    provider: str
    model: str
    error_code: str
    message: str
    usage: TokenUsage | None = None
    audit_events: list[AuditEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(self.message)
