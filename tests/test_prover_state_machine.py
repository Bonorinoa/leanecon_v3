"""Sprint 26 tests for the minimal mathlib-native prover state machine."""

from __future__ import annotations

import pytest

from src.prover.prover import Prover, ProverState, StateMachine


def test_prover_state_enum_order_is_stable() -> None:
    assert [state.value for state in ProverState] == [
        "Synthesizing",
        "Stalled",
        "Decomposing",
        "Rescue",
        "Verified",
        "Failed",
    ]


def test_initial_state_is_synthesizing() -> None:
    machine = StateMachine()
    prover = Prover()

    assert machine.current_state is ProverState.Synthesizing
    assert prover.current_state is ProverState.Synthesizing


def test_valid_state_transitions_work() -> None:
    machine = StateMachine()

    assert machine.transition(ProverState.Stalled, reason="stall detected") is ProverState.Stalled
    assert machine.transition(ProverState.Rescue, reason="unknown identifier") is ProverState.Rescue
    assert (
        machine.transition(ProverState.Synthesizing, reason="rescue completed")
        is ProverState.Synthesizing
    )
    assert machine.transition(ProverState.Verified, reason="goals solved") is ProverState.Verified


def test_invalid_state_transition_raises_clear_error() -> None:
    machine = StateMachine()

    with pytest.raises(ValueError, match="Synthesizing -> Decomposing"):
        machine.transition(ProverState.Decomposing, reason="invalid direct edge")
