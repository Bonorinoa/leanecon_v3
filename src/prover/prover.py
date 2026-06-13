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
    ProverStateTransition,
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
from src.prover.state_machine import ProverState, StateConfig, StateMachine, get_state_config
from src.prover.synthesis import ProverSynthesisMixin, _build_prompt
from src.prover.synthesizer import ProofSynthesizer
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
    "ProverState",
    "StateConfig",
    "ProverTarget",
    "ProverTargetTimeouts",
    "ProverToolInvocation",
    "ProverTraceStep",
    "StateMachine",
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
    "get_state_config",
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
        self._proof_synthesizer = ProofSynthesizer()
        self._state_machine = StateMachine()
        self._state_tool_history_start_index = 0
        self.lsp_client = lsp_client or default_lean_lsp_client
        self._extracted_lemmas = 0
        self._retrieval_events: list[dict[str, Any]] = []
        self._tool_usage_traces: list[dict[str, Any]] = []
        self._state_transitions: list[dict[str, Any]] = []
        self._prover_state_transitions: list[dict[str, Any]] = []
        self._progress_deltas: list[dict[str, Any]] = []
        self._synthesis_events: list[dict[str, Any]] = []
        self._prover_progress_callback: Any | None = None
        self._current_prover_job_id: str | None = None
        self._current_prover_claim_id: str | None = None
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

    @property
    def current_state(self) -> ProverState:
        """Current Sprint 26 prover state for the mathlib-native harness."""
        return self._state_machine.current_state

    @property
    def current_state_config(self) -> StateConfig:
        """Descriptive config for the current Sprint 26 prover state."""
        return self._state_machine.get_current_config()

    def _reset_prover_state(self) -> None:
        """Reset the lightweight prover state machine before a new run."""
        self._state_machine.reset()
        self._state_tool_history_start_index = len(self.budget_tracker.tool_history)

    def _prover_state_metadata(self) -> dict[str, Any]:
        """Return state context suitable for trace/progress metadata."""
        config = self.current_state_config
        state_tool_calls_used = (
            len(self.budget_tracker.tool_history) - self._state_tool_history_start_index
        )
        return {
            "current_state": self.current_state.value,
            "current_state_config": config.to_dict(),
            "state_tool_calls_used": max(0, state_tool_calls_used),
        }

    def _state_allowed_tool_names(self) -> set[str]:
        """Return executable tool names allowed by the current state."""
        return set(self.current_state_config.allowed_tools)

    def _state_tool_limit_reached(self) -> bool:
        """Return whether the current state's configured tool-call cap is spent."""
        max_tool_calls = self.current_state_config.max_tool_calls
        if max_tool_calls is None:
            return False
        used = len(self.budget_tracker.tool_history) - self._state_tool_history_start_index
        return used >= max_tool_calls

    def _state_allows_tool(self, tool_name: str) -> bool:
        """Return whether ``tool_name`` may execute in the current state."""
        return tool_name in self._state_allowed_tool_names()

    def _state_allows_decomposition(self, *, allow_decomposition: bool) -> bool:
        """Combine caller decomposition policy with current state policy."""
        return bool(allow_decomposition and self.current_state_config.allow_decompose)

    def _transition_prover_state(
        self,
        next_state: ProverState,
        *,
        reason: str | None = None,
    ) -> ProverState:
        """Apply a validated prover-state transition.

        The transition graph is intentionally minimal and follows
        ``Sprint26_StateMachine_Design.md``. To preserve existing successful
        paths, no tactic execution or prompt behavior depends on this state yet.
        """
        from_state = self.current_state
        result = self._state_machine.transition(next_state, reason=reason)
        if result == from_state:
            return result
        self._state_tool_history_start_index = len(self.budget_tracker.tool_history)

        state_metadata = self._prover_state_metadata()
        transition_payload = ProverStateTransition(
            from_state=from_state.value,
            to_state=result.value,
            reason=reason,
            current_state_config=state_metadata["current_state_config"],
        ).to_dict()
        self._prover_state_transitions.append(transition_payload)

        callback = self._prover_progress_callback
        if callback is not None and self._current_prover_job_id is not None:
            metadata = {
                **state_metadata,
                "ProverStateTransition": transition_payload,
            }
            callback(
                "prover_state_transition",
                build_progress_event(
                    "prover_state_transition",
                    job_id=self._current_prover_job_id,
                    claim_id=self._current_prover_claim_id,
                    stage="prover",
                    status="running_prover",
                    message=(
                        "Prover state changed "
                        f"{from_state.value} -> {result.value}."
                    ),
                    metadata=metadata,
                ),
            )
        return result

    def _try_transition_prover_state(
        self,
        next_state: ProverState,
        *,
        reason: str | None = None,
    ) -> ProverState:
        """Best-effort state update for instrumentation-only integration."""
        if (
            next_state == self.current_state
            or self._state_machine.can_transition(next_state)
        ):
            return self._transition_prover_state(next_state, reason=reason)
        return self.current_state

DEFAULT_PROVER = Prover()
