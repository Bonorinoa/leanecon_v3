"""Minimal finite-state machine for the mathlib-native prover path.

This module implements the Sprint 26 state set from
``Sprint26_StateMachine_Design.md`` without introducing state-specific tools,
prompts, or memory filters. The state machine is intentionally small: it only
tracks the current strategic prover state and validates explicit transitions.
"""

from __future__ import annotations

from enum import Enum


class ProverState(Enum):
    """Strategic states for the mathlib-native prover harness."""

    Synthesizing = "Synthesizing"
    Stalled = "Stalled"
    Decomposing = "Decomposing"
    Rescue = "Rescue"
    Verified = "Verified"
    Failed = "Failed"


class StateMachine:
    """Validate and track prover state transitions.

    The allowed transitions mirror the initial rules in the Sprint 26 design:
    stalls move synthesis into recovery, rescue is a one-shot detour back to
    synthesis, verified/failed are terminal, and failure is allowed from any
    non-terminal state.
    """

    _ALLOWED_TRANSITIONS: dict[ProverState, frozenset[ProverState]] = {
        ProverState.Synthesizing: frozenset(
            {
                ProverState.Stalled,
                ProverState.Rescue,
                ProverState.Verified,
                ProverState.Failed,
            }
        ),
        ProverState.Stalled: frozenset(
            {
                ProverState.Synthesizing,
                ProverState.Decomposing,
                ProverState.Rescue,
                ProverState.Failed,
            }
        ),
        ProverState.Decomposing: frozenset(
            {
                ProverState.Verified,
                ProverState.Failed,
            }
        ),
        ProverState.Rescue: frozenset(
            {
                ProverState.Synthesizing,
                ProverState.Failed,
            }
        ),
        ProverState.Verified: frozenset(),
        ProverState.Failed: frozenset(),
    }

    def __init__(self, initial_state: ProverState = ProverState.Synthesizing) -> None:
        self.current_state = initial_state

    def reset(self, state: ProverState = ProverState.Synthesizing) -> None:
        """Reset the current state for a fresh prover run."""
        self.current_state = state

    def can_transition(self, next_state: ProverState) -> bool:
        """Return whether ``next_state`` is valid from the current state."""
        return next_state in self._ALLOWED_TRANSITIONS[self.current_state]

    def transition(self, next_state: ProverState, *, reason: str | None = None) -> ProverState:
        """Move to ``next_state`` or raise ``ValueError`` for an invalid edge.

        ``reason`` is accepted for call-site readability and future
        observability, but this minimal implementation does not persist it.
        """
        del reason
        if next_state == self.current_state:
            return self.current_state
        if not self.can_transition(next_state):
            raise ValueError(
                f"Invalid prover state transition: "
                f"{self.current_state.value} -> {next_state.value}"
            )
        self.current_state = next_state
        return self.current_state
