"""Tests for the minimal mathlib-native prover state machine."""

from __future__ import annotations

import json

import pytest

from src.memory.models import ProofTrace
from src.observability.models import ProgressDelta, ProverStateTransition
from src.prover.models import ProverAction, ProverTarget, ProverToolInvocation
from src.prover.prover import Prover, ProverState, StateMachine, get_state_config
from src.prover.synthesis import _build_prompt
from src.prover.tactics import should_decompose
from tests.test_prover import _packet


def _trace(
    claim_id: str,
    *,
    outcome: str,
    failure_class: str | None = None,
    repair_count: int = 0,
    preamble_names: list[str] | None = None,
    trace_metadata: dict[str, object] | None = None,
) -> ProofTrace:
    return ProofTrace(
        claim_id=claim_id,
        claim_text=f"claim {claim_id}",
        preamble_names=preamble_names or ["fixed_point_theorem"],
        tactic_sequence=[f"exact {claim_id}"],
        stage_outcomes={"prover": outcome},
        failure_class=failure_class,
        repair_count=repair_count,
        outcome=outcome,
        formalizer_model="test",
        timestamp=claim_id,
        lesson_summary=f"lesson {claim_id}",
        trace_metadata=trace_metadata,
    )


class _RecordingTraceStore:
    def __init__(
        self,
        traces: list[ProofTrace],
        *,
        helper_traces: list[ProofTrace] | None = None,
    ) -> None:
        self.traces = traces
        self.helper_traces = helper_traces or []
        self.similar_calls: list[dict[str, object]] = []
        self.helper_calls: list[dict[str, object]] = []

    def query_similar(
        self,
        preamble_names: list[str],
        limit: int = 3,
        *,
        outcome: str | None = None,
    ) -> list[ProofTrace]:
        self.similar_calls.append(
            {
                "preamble_names": list(preamble_names),
                "limit": limit,
                "outcome": outcome,
            }
        )
        matched = [
            trace for trace in self.traces if set(trace.preamble_names).intersection(preamble_names)
        ]
        if outcome is not None:
            matched = [trace for trace in matched if trace.outcome == outcome]
        return matched[:limit]

    def query_mathlib_helpers(
        self,
        concept_tokens: list[str],
        limit: int = 2,
    ) -> list[ProofTrace]:
        self.helper_calls.append(
            {
                "concept_tokens": list(concept_tokens),
                "limit": limit,
            }
        )
        return self.helper_traces[:limit]


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


@pytest.mark.parametrize(
    ("start", "valid_next_states"),
    [
        (
            ProverState.Synthesizing,
            {
                ProverState.Synthesizing,
                ProverState.Stalled,
                ProverState.Rescue,
                ProverState.Verified,
                ProverState.Failed,
            },
        ),
        (
            ProverState.Stalled,
            {
                ProverState.Synthesizing,
                ProverState.Stalled,
                ProverState.Decomposing,
                ProverState.Rescue,
                ProverState.Failed,
            },
        ),
        (
            ProverState.Decomposing,
            {
                ProverState.Decomposing,
                ProverState.Verified,
                ProverState.Failed,
            },
        ),
        (
            ProverState.Rescue,
            {
                ProverState.Synthesizing,
                ProverState.Rescue,
                ProverState.Failed,
            },
        ),
        (ProverState.Verified, {ProverState.Verified}),
        (ProverState.Failed, {ProverState.Failed}),
    ],
)
def test_state_transition_matrix_is_enforced(
    start: ProverState,
    valid_next_states: set[ProverState],
) -> None:
    all_states = set(ProverState)

    for next_state in valid_next_states:
        machine = StateMachine(start)
        assert machine.transition(next_state, reason="matrix check") is next_state

    for next_state in all_states - valid_next_states:
        machine = StateMachine(start)
        assert not machine.can_transition(next_state)
        with pytest.raises(
            ValueError,
            match=f"{start.value} -> {next_state.value}",
        ):
            machine.transition(next_state, reason="matrix check")


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
    config.prompt_rules["mode"] = "mutated"

    current_config = machine.get_current_config()
    assert current_config.allowed_tools == [
        "apply_tactic",
        "get_goals",
        "lean_leansearch",
        "lean_local_search",
    ]
    assert current_config.prompt_rules["mode"] == "rescue"


def test_get_state_config_returns_independent_config_copy() -> None:
    config = get_state_config(ProverState.Stalled)
    config.allowed_tools.clear()
    config.prompt_rules["mode"] = "mutated"

    fresh_config = get_state_config(ProverState.Stalled)
    assert "apply_tactic" in fresh_config.allowed_tools
    assert fresh_config.prompt_rules["mode"] == "recover_from_stall"
    assert fresh_config.memory_filter == "failure_focused"


def test_state_config_to_dict_is_json_ready_copy() -> None:
    config = get_state_config(ProverState.Rescue)
    payload = config.to_dict()
    payload["allowed_tools"].append("mutated")
    payload["prompt_rules"]["mode"] = "mutated"

    assert payload["memory_filter"] == "rescue_identifier"
    assert payload["max_tool_calls"] == 2
    assert get_state_config(ProverState.Rescue).prompt_rules["mode"] == "rescue"
    assert "mutated" not in get_state_config(ProverState.Rescue).allowed_tools


def test_get_current_config_tracks_transitions_and_reset() -> None:
    machine = StateMachine()

    assert machine.get_current_config() == get_state_config(ProverState.Synthesizing)
    machine.transition(ProverState.Stalled, reason="stall detected")
    assert machine.get_current_config() == get_state_config(ProverState.Stalled)
    machine.reset(ProverState.Rescue)
    assert machine.current_state is ProverState.Rescue
    assert machine.get_current_config() == get_state_config(ProverState.Rescue)


def test_prover_state_transition_creation_and_fields() -> None:
    config = get_state_config(ProverState.Stalled)

    transition = ProverStateTransition(
        from_state=ProverState.Synthesizing.value,
        to_state=ProverState.Stalled.value,
        reason="stall detected",
        current_state_config={
            "memory_filter": config.memory_filter,
            "prompt_rules": config.prompt_rules,
        },
        timestamp="2026-06-10T00:00:00+00:00",
    )
    payload = transition.to_dict()

    assert payload == {
        "event_type": "ProverStateTransition",
        "from_state": "Synthesizing",
        "to_state": "Stalled",
        "reason": "stall detected",
        "timestamp": "2026-06-10T00:00:00+00:00",
        "current_state": "Stalled",
        "current_state_config": {
            "memory_filter": "failure_focused",
            "prompt_rules": {
                "mode": "recover_from_stall",
                "guidance": (
                    "Inspect the current goal and recent failure before trying a new tactic."
                ),
            },
        },
    }


def test_mathlib_stall_helpers_round_trip_to_synthesizing() -> None:
    prover = Prover()

    stalled_config = prover._enter_mathlib_stalled_state(reason="ProgressDelta.stall_detected")
    assert prover.current_state is ProverState.Stalled
    assert stalled_config == get_state_config(ProverState.Stalled)

    recovered_config = prover._recover_mathlib_stall(reason="refined retrieval returned premises")
    assert prover.current_state is ProverState.Synthesizing
    assert recovered_config == get_state_config(ProverState.Synthesizing)


def test_prover_state_transition_observability_payload_is_recorded() -> None:
    prover = Prover()
    emitted: list[tuple[str, dict[str, object]]] = []
    prover._prover_progress_callback = (
        lambda event, payload: emitted.append((event, payload))
    )
    prover._current_prover_job_id = "job_state"
    prover._current_prover_claim_id = "claim_state"

    prover._enter_mathlib_stalled_state(reason="ProgressDelta.stall_detected")

    assert prover._prover_state_transitions
    transition = prover._prover_state_transitions[-1]
    assert transition["event_type"] == "ProverStateTransition"
    assert transition["from_state"] == "Synthesizing"
    assert transition["to_state"] == "Stalled"
    assert transition["reason"] == "ProgressDelta.stall_detected"
    assert transition["current_state"] == "Stalled"
    assert transition["current_state_config"]["memory_filter"] == "failure_focused"
    assert transition["current_state_config"]["prompt_rules"]["mode"] == "recover_from_stall"
    assert emitted
    event_name, progress = emitted[-1]
    assert event_name == "prover_state_transition"
    metadata = progress["metadata"]
    assert metadata["current_state"] == "Stalled"
    assert metadata["current_state_config"] == transition["current_state_config"]
    assert metadata["ProverStateTransition"] == transition


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


def test_synthesizing_harness_prompt_keeps_default_shape() -> None:
    prover = Prover()
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )
    target = ProverTarget(
        name="t",
        statement="True",
        kind="theorem_body",
        helper_theorem_name="t",
    )

    prompt = prover._build_mathlib_harness_prompt(
        packet=packet,
        target=target,
        state={"code": "...", "goals": ["⊢ True"]},
        retrieved_premises=[],
        diagnostics=[],
        code_actions=[],
        prior_trace=[],
    )
    body = json.loads(prompt)

    assert "state_context" not in body
    assert "state_prompt_rules" not in body["instructions"]
    assert "state_memory_filter" not in body["instructions"]


def test_generic_prompt_uses_state_rules_only_for_non_default_states() -> None:
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
    )
    target = ProverTarget(
        name="t",
        statement="True",
        kind="theorem_body",
        helper_theorem_name="t",
    )

    default_prompt = json.loads(
        _build_prompt(
            packet=packet,
            target=target,
            current_code="theorem t : True := by trivial",
            tool_specs=[],
            lean_feedback=[],
            goals=["⊢ True"],
            prior_trace=[],
            examples=[],
            current_state=ProverState.Synthesizing,
            state_config=get_state_config(ProverState.Synthesizing),
        )
    )
    rescue_prompt = json.loads(
        _build_prompt(
            packet=packet,
            target=target,
            current_code="theorem t : True := by trivial",
            tool_specs=[],
            lean_feedback=["unknown identifier"],
            goals=["⊢ True"],
            prior_trace=[],
            examples=[],
            current_state=ProverState.Rescue,
            state_config=get_state_config(ProverState.Rescue),
        )
    )

    assert "state_context" not in default_prompt
    assert rescue_prompt["state_context"]["current_state"] == "Rescue"
    assert rescue_prompt["instructions"]["state_prompt_rules"]["mode"] == "rescue"
    assert rescue_prompt["instructions"]["state_memory_filter"] == "rescue_identifier"
    assert "allowed_tools" not in rescue_prompt["state_context"]


@pytest.mark.parametrize(
    ("state", "expected_filter", "expected_mode"),
    [
        (ProverState.Stalled, "failure_focused", "recover_from_stall"),
        (ProverState.Decomposing, "subgoal_focused", "decompose"),
        (ProverState.Rescue, "rescue_identifier", "rescue"),
    ],
)
def test_recovery_state_prompts_include_state_rules(
    state: ProverState,
    expected_filter: str,
    expected_mode: str,
) -> None:
    prover = Prover()
    if state is ProverState.Stalled:
        prover._enter_mathlib_stalled_state(reason="stall detected")
    elif state is ProverState.Decomposing:
        prover._enter_mathlib_stalled_state(reason="stall detected")
        prover._recover_mathlib_stall(
            next_state=ProverState.Decomposing,
            reason="split into helper lemma",
        )
    else:
        prover._enter_mathlib_rescue_state(reason="unknown identifier")
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )
    target = ProverTarget(
        name="t",
        statement="True",
        kind="theorem_body",
        helper_theorem_name="t",
    )

    prompt = prover._build_mathlib_harness_prompt(
        packet=packet,
        target=target,
        state={"code": "...", "goals": ["⊢ True"]},
        retrieved_premises=[],
        diagnostics=[],
        code_actions=[],
        prior_trace=[],
    )
    body = json.loads(prompt)

    assert body["state_context"]["current_state"] == state.value
    assert body["state_context"]["memory_filter"] == expected_filter
    assert body["state_context"]["prompt_rules"]["mode"] == expected_mode
    assert body["instructions"]["state_prompt_rules"]["mode"] == expected_mode
    assert body["instructions"]["state_memory_filter"] == expected_filter
    assert "allowed_tools" not in body["state_context"]
    assert body["state_context"]["prompt_rules"]["guidance"] in body["instructions"]["rules"]


def test_state_memory_filter_changes_retrieval_strategy() -> None:
    store = _RecordingTraceStore(
        [
            _trace("verified", outcome="verified"),
            _trace("failed", outcome="failed", failure_class="no_progress", repair_count=2),
        ]
    )
    prover = Prover(trace_store=store)
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        selected_preamble=["fixed_point_theorem"],
    )

    synthesizing_examples = prover._memory_examples(packet)
    prover._enter_mathlib_stalled_state(reason="stall detected")
    stalled_examples = prover._memory_examples(packet)

    assert store.similar_calls[0] == {
        "preamble_names": ["fixed_point_theorem"],
        "limit": 2,
        "outcome": "verified",
    }
    assert synthesizing_examples[0]["outcome"] == "verified"
    assert store.similar_calls[1] == {
        "preamble_names": ["fixed_point_theorem"],
        "limit": 6,
        "outcome": None,
    }
    assert stalled_examples[0]["outcome"] == "failed"
    assert stalled_examples[0]["trace_metadata"] == {}


def test_decomposing_and_rescue_memory_use_narrow_helper_retrieval() -> None:
    helper = _trace(
        "helper",
        outcome="verified",
        preamble_names=[],
        trace_metadata={"memory_kind": "mathlib_helper_lemma"},
    )
    store = _RecordingTraceStore([], helper_traces=[helper])
    prover = Prover(trace_store=store)
    packet = _packet(
        theorem_name="helper_target",
        claim="Tendsto helper target",
        lean_code="theorem helper_target : True := by trivial",
        claim_type="mathlib_native",
    )

    prover._enter_mathlib_stalled_state(reason="stall detected")
    prover._recover_mathlib_stall(
        next_state=ProverState.Decomposing,
        reason="split helper",
    )
    decomposing_examples = prover._memory_examples(packet)
    prover = Prover(trace_store=store)
    prover._enter_mathlib_rescue_state(reason="unknown identifier")
    rescue_examples = prover._memory_examples(packet)

    assert decomposing_examples[0]["trace_metadata"]["memory_kind"] == ("mathlib_helper_lemma")
    assert rescue_examples[0]["trace_metadata"]["memory_kind"] == "mathlib_helper_lemma"
    assert store.helper_calls[0]["limit"] == 2
    assert store.helper_calls[1]["limit"] == 1


def test_state_aware_tool_specs_follow_current_state_allowlist() -> None:
    prover = Prover()
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )

    prover._enter_mathlib_rescue_state(reason="unknown identifier")
    tool_names = {spec["name"] for spec in prover._tool_specs_for_prompt(packet)}

    assert tool_names == set(get_state_config(ProverState.Rescue).allowed_tools)


def test_state_policy_blocks_disallowed_tool_before_budget_recording() -> None:
    prover = Prover()
    target = ProverTarget(
        name="theorem_body",
        statement="True",
        kind="theorem_body",
        helper_theorem_name="t",
    )
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )

    prover._enter_mathlib_rescue_state(reason="unknown identifier")
    result = prover._execute_tool(
        session=object(),  # type: ignore[arg-type]
        tool=ProverToolInvocation(name="write_current_code", arguments={"code": ""}),
        packet=packet,
        target=target,
    )

    assert result.is_error is True
    assert "state_policy_violation" in result.content
    assert prover.budget_tracker.tool_history == []


def test_state_max_tool_calls_blocks_allowed_tool_after_state_budget_spent() -> None:
    prover = Prover()
    target = ProverTarget(
        name="theorem_body",
        statement="True",
        kind="theorem_body",
        helper_theorem_name="t",
    )
    packet = _packet(
        theorem_name="t",
        claim="simple claim",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )

    prover._enter_mathlib_rescue_state(reason="unknown identifier")
    prover.budget_tracker.tool_history.extend(["apply_tactic", "get_goals"])
    prover.budget_tracker.total_tool_calls = 2
    result = prover._execute_tool(
        session=object(),  # type: ignore[arg-type]
        tool=ProverToolInvocation(name="lean_leansearch", arguments={"query": "true"}),
        packet=packet,
        target=target,
    )

    assert result.is_error is True
    assert "state_tool_budget_exhausted" in result.content
    assert prover.budget_tracker.tool_history == ["apply_tactic", "get_goals"]


def test_rescue_state_blocks_decomposition_gate() -> None:
    prover = Prover()
    prover._enter_mathlib_rescue_state(reason="unknown identifier")
    action = ProverAction.model_validate(
        {
            "action_type": "decompose",
            "rationale": "try a helper",
            "decomposition_name": "helper",
            "decomposition_statement": "True",
        }
    )

    assert not prover._state_allows_decomposition(allow_decomposition=True)
    assert not should_decompose(
        failed_turns_for_target=2,
        action=action,
        allow_decomposition=prover._state_allows_decomposition(allow_decomposition=True),
        current_depth=0,
        total_extracted=0,
        no_progress_streak=1,
        direct_candidates_available=False,
        max_recursion_depth=3,
    )
