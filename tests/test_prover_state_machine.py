"""Sprint 26 tests for the minimal mathlib-native prover state machine."""

from __future__ import annotations

import pytest

from src.observability.models import ProgressDelta
from src.prover.prover import Prover, ProverState, StateMachine, get_state_config


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
    assert prover.current_state_config == get_state_config(ProverState.Synthesizing)


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


@pytest.mark.parametrize("state", list(ProverState))
def test_state_config_exists_for_every_state(state: ProverState) -> None:
    config = get_state_config(state)

    assert isinstance(config.allowed_tools, list)
    assert isinstance(config.prompt_rules, dict)
    assert isinstance(config.memory_filter, str)
    assert config.max_tool_calls is None or config.max_tool_calls >= 0


def test_synthesizing_config_is_default_permissive() -> None:
    config = get_state_config(ProverState.Synthesizing)

    assert "apply_tactic" in config.allowed_tools
    assert "lean_leansearch" in config.allowed_tools
    assert "write_current_code" in config.allowed_tools
    assert config.allow_decompose is True
    assert config.max_tool_calls is None
    assert config.terminal is False


def test_stalled_config_allows_recovery_and_decomposition() -> None:
    config = get_state_config(ProverState.Stalled)

    assert "apply_tactic" in config.allowed_tools
    assert "lean_leansearch" in config.allowed_tools
    assert config.memory_filter == "failure_focused"
    assert config.allow_decompose is True
    assert config.max_tool_calls == 4


def test_decomposing_config_is_subgoal_focused() -> None:
    config = get_state_config(ProverState.Decomposing)

    assert "write_current_code" in config.allowed_tools
    assert config.memory_filter == "subgoal_focused"
    assert config.allow_decompose is True
    assert config.max_tool_calls == 6


def test_rescue_config_is_narrow() -> None:
    config = get_state_config(ProverState.Rescue)

    assert config.allowed_tools == [
        "apply_tactic",
        "get_goals",
        "lean_leansearch",
        "lean_local_search",
    ]
    assert config.memory_filter == "rescue_identifier"
    assert config.allow_decompose is False
    assert config.max_tool_calls == 2


@pytest.mark.parametrize("state", [ProverState.Verified, ProverState.Failed])
def test_terminal_state_configs_are_empty(state: ProverState) -> None:
    config = get_state_config(state)

    assert config.allowed_tools == []
    assert config.memory_filter == "none"
    assert config.max_tool_calls == 0
    assert config.allow_decompose is False
    assert config.terminal is True


def test_state_machine_returns_current_config_copy() -> None:
    machine = StateMachine(ProverState.Rescue)
    config = machine.get_current_config()
    config.allowed_tools.append("mutated")

    assert machine.get_current_config().allowed_tools == [
        "apply_tactic",
        "get_goals",
        "lean_leansearch",
        "lean_local_search",
    ]


def test_mathlib_stall_helpers_round_trip_to_synthesizing() -> None:
    prover = Prover()

    stalled_config = prover._enter_mathlib_stalled_state(
        reason="ProgressDelta.stall_detected"
    )
    assert prover.current_state is ProverState.Stalled
    assert stalled_config == get_state_config(ProverState.Stalled)

    recovered_config = prover._recover_mathlib_stall(
        reason="refined retrieval returned premises"
    )
    assert prover.current_state is ProverState.Synthesizing
    assert recovered_config == get_state_config(ProverState.Synthesizing)


def test_mathlib_stall_can_recover_into_decomposition() -> None:
    prover = Prover()

    prover._enter_mathlib_stalled_state(reason="ProgressDelta.stall_detected")
    decomposing_config = prover._recover_mathlib_stall(
        next_state=ProverState.Decomposing,
        reason="helper lemma decomposition started",
    )

    assert prover.current_state is ProverState.Decomposing
    assert decomposing_config == get_state_config(ProverState.Decomposing)


def test_mathlib_rescue_helpers_round_trip_to_synthesizing() -> None:
    prover = Prover()

    rescue_config = prover._enter_mathlib_rescue_state(reason="unknown identifier")
    assert prover.current_state is ProverState.Rescue
    assert rescue_config == get_state_config(ProverState.Rescue)

    recovered_config = prover._recover_mathlib_rescue(reason="rescue retrieval completed")
    assert prover.current_state is ProverState.Synthesizing
    assert recovered_config == get_state_config(ProverState.Synthesizing)


def test_mathlib_stalled_gate_uses_explicit_stall_detected_flag() -> None:
    prover = Prover()
    unchanged_but_not_stalled = ProgressDelta(
        goals_reduced=False,
        stall_detected=False,
    )
    stalled = ProgressDelta(
        goals_reduced=False,
        stall_detected=True,
    )

    assert not prover._should_enter_mathlib_stalled_state(
        last_delta=unchanged_but_not_stalled,
        budget_remaining_frac=0.5,
    )
    assert prover._should_enter_mathlib_stalled_state(
        last_delta=stalled,
        budget_remaining_frac=0.5,
    )
