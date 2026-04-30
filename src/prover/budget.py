"""Budget and claim-type policy helpers for Prover.

The mixin here centralizes tool-budget reset and mathlib-native policy limits so
execution code can ask for a policy without duplicating threshold decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.formalizer.models import FormalizationPacket

SHORTCUT_ATTEMPT_TIMEOUT_SECONDS = 25

MAX_DIRECT_CLOSURE_CANDIDATES = 24

POST_DIRECT_CLOSURE_STALL_LIMIT = 2

SHALLOW_LOOP_WINDOW = 4

SHORTCUT_FALLBACK_TACTICS: tuple[tuple[str, str], ...] = (
    ("assumption", "Goal matches a local hypothesis; closing via `assumption`."),
    ("rfl", "Goal closes by definitional reflexivity."),
    ("exact?", "Library search closed the goal via `exact?`."),
    ("decide", "Goal is decidable; closing via `decide`."),
    ("norm_num", "Numerical goal closes via `norm_num`."),
    ("simp", "Goal closes after `simp` normalization."),
    ("linarith", "Linear-arithmetic closure via `linarith`."),
)

_PROGRESS_CODE_WINDOW = 240

_HINT_FIRST_PREAMBLES = frozenset(
    {
        "continuous_preference",
        "convex_preference",
        "constrained_optimization",
        "kuhn_tucker",
        "value_function",
    }
)

_WRAPPER_SIMPA_SHAPES = frozenset({"Monotone", "Antitone", "StrictMono", "StrictAnti"})

_RECOGNIZED_CLAIM_TYPES = frozenset({"preamble_definable", "mathlib_native"})

MATHLIB_NATIVE_DIRECT_CLOSE_LIMIT = 2

MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT = 6

MATHLIB_NATIVE_LSP_SEARCH_RESULTS = 8

MATHLIB_NATIVE_PROMPT_ONLY_TOOLS = frozenset(
    {
        "lean_diagnostic_messages",
        "lean_leansearch",
        "lean_loogle",
        "lean_local_search",
        "lean_file_outline",
    }
)

ClaimType = Literal["preamble_definable", "mathlib_native"]

@dataclass(frozen=True)
class DirectClosePolicy:
    claim_type: ClaimType | None
    claim_type_policy: str
    attempt_cap: int
    preamble_shortcuts_enabled: bool

@dataclass(frozen=True)
class DirectCloseAttemptSummary:
    candidate_count: int
    attempt_limit: int
    attempts_used: int
    claim_type: ClaimType | None
    claim_type_policy: str
    preamble_shortcuts_enabled: bool

    @property
    def exhausted(self) -> bool:
        return self.candidate_count > 0 and self.attempts_used >= self.attempt_limit

    def metadata(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "attempt_limit": self.attempt_limit,
            "attempts_used": self.attempts_used,
            "claim_type": self.claim_type,
            "claim_type_policy": self.claim_type_policy,
            "preamble_shortcuts_enabled": self.preamble_shortcuts_enabled,
        }

class ProverBudgetMixin:

    """Mixin extracted from the legacy Prover monolith."""

    def _normalized_claim_type(self, packet: FormalizationPacket) -> ClaimType | None:
        claim_type = getattr(packet, "claim_type", None)
        if claim_type in _RECOGNIZED_CLAIM_TYPES:
            return claim_type
        return None

    def _direct_close_policy(self, packet: FormalizationPacket) -> DirectClosePolicy:
        # Keep claim-type handling centralized: mathlib-native claims may use a
        # small compile-checked direct-close budget, but Preamble-derived
        # shortcuts stay disabled so failures honestly reflect missing Mathlib
        # search strategy rather than accidental LeanEcon lemma reuse.
        claim_type = self._normalized_claim_type(packet)
        if claim_type == "mathlib_native":
            return DirectClosePolicy(
                claim_type=claim_type,
                claim_type_policy="mathlib_native_cap_2_no_preamble_shortcuts",
                attempt_cap=MATHLIB_NATIVE_DIRECT_CLOSE_LIMIT,
                preamble_shortcuts_enabled=False,
            )
        if claim_type == "preamble_definable":
            return DirectClosePolicy(
                claim_type=claim_type,
                claim_type_policy="preamble_definable_default",
                attempt_cap=MAX_DIRECT_CLOSURE_CANDIDATES,
                preamble_shortcuts_enabled=True,
            )
        return DirectClosePolicy(
            claim_type=None,
            claim_type_policy="default",
            attempt_cap=MAX_DIRECT_CLOSURE_CANDIDATES,
            preamble_shortcuts_enabled=True,
        )

    def _budget_remaining_frac(self) -> float:
        max_search = max(1, int(self.budget_tracker.max_search_tool_calls))
        used = int(self.budget_tracker.search_tool_calls)
        return max(0.0, 1.0 - used / max_search)

    def _apply_budget_limits_for_packet(self, packet: FormalizationPacket) -> None:
        """Sprint 23 Task 3: hybrid budget bump for mathlib_native claims only."""
        from src.config import (
            MAX_PROVE_STEPS,
            MAX_PROVE_STEPS_HYBRID,
            MAX_SEARCH_TOOL_CALLS,
            MAX_SEARCH_TOOL_CALLS_HYBRID,
            MAX_TOTAL_TOOL_CALLS,
        )

        # Restore defaults first so a prior mathlib_native call doesn't leak.
        self.budget_tracker.max_search_tool_calls = MAX_SEARCH_TOOL_CALLS
        self.budget_tracker.max_total_tool_calls = MAX_TOTAL_TOOL_CALLS
        if self._normalized_claim_type(packet) == "mathlib_native":
            self.budget_tracker.max_search_tool_calls = MAX_SEARCH_TOOL_CALLS_HYBRID
            # Total is "search + everything else"; keep the existing total ceiling
            # but allow the hybrid prove-step extension via the env-overridable
            # constant for callers that consult it directly. (Steps are governed
            # by max_turns, so the constant is informational for now.)
            _ = MAX_PROVE_STEPS, MAX_PROVE_STEPS_HYBRID  # exported for tests/config inspection

    def _reset_budget_tracker(self) -> None:
        self.budget_tracker.search_tool_calls = 0
        self.budget_tracker.total_tool_calls = 0
        self.budget_tracker.lsp_tool_calls = 0
        self.budget_tracker.native_search_attempts = 0
        self.budget_tracker.mathlib_native_mode_uses = 0
        self.budget_tracker.sub_agent_calls = 0
        self.budget_tracker.tool_history.clear()
        self.budget_tracker.sub_agent_history.clear()
        # Sprint 24: clear per-invocation enrichment caches via the LSP cache.
        if self._lsp_cache is not None:
            self._lsp_cache.clear()
