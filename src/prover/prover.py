"""Public prover orchestrator with compatibility exports.

The implementation is split into focused mixins under ``src.prover``. This
module preserves the historical import path and monkeypatch surface used by the
harness and tests.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any

from src.config import PROVER_BACKEND, PROVER_FALLBACK_BACKEND
from src.lean import LeanREPLSession, compile_check, lean_run_code
from src.memory import ProofTraceStore, trace_store as default_trace_store
from src.observability import (
    AuditEvent,
    BudgetTracker,
    LeanLSPClient,
    LeanLSPUnavailableError,
    build_progress_event,
    default_lean_lsp_client,
    log_event,
)
from src.prover.budget import DirectCloseAttemptSummary, DirectClosePolicy, ProverBudgetMixin
from src.prover.drivers import (
    DriverRegistry,
    HuggingFaceProverDriver,
    MistralProverDriver,
    PROVER_RETRY_ATTEMPTS,
    PROVER_RETRY_BACKOFF_SECONDS,
    PROVER_RETRYABLE_ERROR_CODES,
    ProverBackend,
    ProverDriver,
    ProverDriverError,
)
from src.prover.error_handling import ProverErrorHandlingMixin
from src.prover.execution import (
    ProverExecutionMixin,
    _ActiveProofSession,
    _count_standalone_sorries,
    _extract_theorem_block,
    _inject_theorem_before_main,
    _proof_body_fingerprint,
    _replace_last_sorry,
    _replace_named_theorem_body,
    _replace_subgoal_with_helper,
    _standalone_theorem_code,
)
from src.prover.file_controller import ProofFileController
from src.prover.lsp_cache import LSPCache
from src.prover.memory_writer import ProverMemoryWriter
from src.prover.models import (
    ProverAction,
    ProverFailure,
    ProverResult,
    ProverTarget,
    ProverTargetTimeouts,
    ProverToolInvocation,
    ProverTraceStep,
)
from src.prover.retrieval import (
    ProverRetrievalMixin,
    _contains_lsp_unavailable,
    _extract_mathlib_idents,
    _extract_unknown_identifier,
    _query_from_failed_identifier,
)
from src.prover.synthesis import ProverSynthesisMixin, _build_prompt
from src.prover.tactics import direct_hypothesis_name, suggest_fast_path_tactics
from src.tools import ToolRegistry, build_default_registry

__all__ = [
    "AuditEvent",
    "DEFAULT_PROVER",
    "DirectCloseAttemptSummary",
    "DirectClosePolicy",
    "DriverRegistry",
    "HuggingFaceProverDriver",
    "LeanLSPUnavailableError",
    "LeanREPLSession",
    "MistralProverDriver",
    "PROVER_RETRY_ATTEMPTS",
    "PROVER_RETRY_BACKOFF_SECONDS",
    "PROVER_RETRYABLE_ERROR_CODES",
    "Prover",
    "ProverAction",
    "ProverBackend",
    "ProverDriver",
    "ProverDriverError",
    "ProverFailure",
    "ProverResult",
    "ProverTarget",
    "ProverTargetTimeouts",
    "ProverToolInvocation",
    "ProverTraceStep",
    "_ActiveProofSession",
    "_build_prompt",
    "_contains_lsp_unavailable",
    "_count_standalone_sorries",
    "_extract_mathlib_idents",
    "_extract_theorem_block",
    "_extract_unknown_identifier",
    "_inject_theorem_before_main",
    "_proof_body_fingerprint",
    "_query_from_failed_identifier",
    "_replace_last_sorry",
    "_replace_named_theorem_body",
    "_replace_subgoal_with_helper",
    "_standalone_theorem_code",
    "build_progress_event",
    "compile_check",
    "direct_hypothesis_name",
    "lean_run_code",
    "log_event",
    "suggest_fast_path_tactics",
    "time",
    "urllib",
]


class Prover(
    ProverExecutionMixin,
    ProverRetrievalMixin,
    ProverSynthesisMixin,
    ProverBudgetMixin,
    ProverErrorHandlingMixin,
):
    """Main proving orchestrator with self-correction and bounded decomposition."""

    def __init__(
        self,
        *,
        backend: str = PROVER_BACKEND,
        huggingface_driver: ProverDriver | None = None,
        mistral_driver: ProverDriver | None = None,
        registry: ToolRegistry | None = None,
        file_controller: ProofFileController | None = None,
        trace_store: ProofTraceStore | None = None,
        budget_tracker: BudgetTracker | None = None,
        lsp_client: LeanLSPClient | None = None,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.driver_registry = DriverRegistry()
        self.primary_backend = self.driver_registry.get(backend)
        fallback_name = PROVER_FALLBACK_BACKEND
        if fallback_name not in self.driver_registry.available():
            fallback_name = "leanstral"
        self.fallback_backend = self.driver_registry.get(fallback_name)
        self._drivers: dict[str, ProverDriver] = {
            "huggingface": huggingface_driver or HuggingFaceProverDriver(),
            "mistral": mistral_driver or MistralProverDriver(),
        }
        self.file_controller = file_controller or ProofFileController()
        self.trace_store = trace_store or default_trace_store
        self.budget_tracker = budget_tracker or BudgetTracker()
        self.memory_writer = ProverMemoryWriter(self.trace_store)
        self.lsp_client = lsp_client or default_lean_lsp_client
        self._extracted_lemmas = 0
        self._retrieval_events: list[dict[str, Any]] = []
        self._tool_usage_traces: list[dict[str, Any]] = []
        self._state_transitions: list[dict[str, Any]] = []
        self._progress_deltas: list[dict[str, Any]] = []
        # Stage 2-followup C: track which (claim_id, target_name) pairs have
        # already triggered a second-pass refined retrieval. Ensures we fire
        # at most once per target even after dropping the strict turn==1 gate.
        self._second_retrieval_targets: set[tuple[str, str]] = set()
        # Stage 2-followup D: track which (claim_id, target_name) pairs have
        # already had an unknown-identifier rescue retrieval, so the rescue
        # fires at most once per target.
        self._rescue_retrieval_targets: set[tuple[str, str]] = set()
        # Sprint 24: per-prove LSP outline/hover cache + premise enrichment.
        # The cache is constructed lazily via ``_get_lsp_cache`` because
        # ``self.lsp_client`` may be swapped by tests *after* ``__init__``.
        self._lsp_cache: LSPCache | None = None

DEFAULT_PROVER = Prover()
