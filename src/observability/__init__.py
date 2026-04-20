"""Observability exports."""

from .errors import StageExecutionError, classify_exception
from .lean_lsp_client import LeanLSPClient, LeanLSPUnavailableError, default_lean_lsp_client
from .logging import log_event
from .models import AuditEvent, ProviderCallMetadata, StageRunResult, StageTiming, TokenUsage
from .pricing import complete_usage, dump_pricing_registry, estimate_tokens_from_text, lookup_pricing, stable_hash_text
from .sse import encode_sse
from .telemetry import (
    SpanRecorder,
    attach_telemetry,
    dominant_failure_class,
    preamble_names_from_theorem,
    sync_classified_errors,
    tactic_sequence_from_attempts,
    utc_now,
    verification_outcome,
)
from .tool_tracker import BudgetTracker

__all__ = [
    "BudgetTracker",
    "AuditEvent",
    "LeanLSPClient",
    "LeanLSPUnavailableError",
    "ProviderCallMetadata",
    "SpanRecorder",
    "StageExecutionError",
    "StageRunResult",
    "StageTiming",
    "TokenUsage",
    "attach_telemetry",
    "classify_exception",
    "complete_usage",
    "default_lean_lsp_client",
    "dominant_failure_class",
    "dump_pricing_registry",
    "estimate_tokens_from_text",
    "encode_sse",
    "lookup_pricing",
    "log_event",
    "preamble_names_from_theorem",
    "sync_classified_errors",
    "stable_hash_text",
    "tactic_sequence_from_attempts",
    "utc_now",
    "verification_outcome",
]
