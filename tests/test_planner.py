from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.memory.models import ProofTrace
from src.memory.store import ProofTraceStore
from src.planner import HuggingFacePlannerDriver, PlannerBackend, PlannerLLMResponse, PlannerService
from src.planner.retrieval import HashingTextEmbedder, PlannerRetrievalService


class FakePlannerDriver:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def generate(self, **_: object) -> PlannerLLMResponse:
        return PlannerLLMResponse.model_validate(self.payload)


def _make_trace_store(tmp_path: Path) -> ProofTraceStore:
    store = ProofTraceStore(tmp_path / "planner-memory.db")
    store.record(
        ProofTrace(
            claim_id="trace-1",
            claim_text="Show the Bellman operator has a unique fixed point in a discounted dynamic program.",
            preamble_names=["bellman_operator", "contraction_mapping", "value_function"],
            tactic_sequence=["apply contraction_has_fixedPoint", "simpa", "exact hbeta"],
            stage_outcomes={"formalizer": "ok", "prover": "verified"},
            failure_class=None,
            repair_count=0,
            outcome="verified",
            formalizer_model="mistralai/Leanstral-2603",
            timestamp="2026-04-19T10:00:00+00:00",
        )
    )
    store.record(
        ProofTrace(
            claim_id="trace-2",
            claim_text="Establish Bellman monotonicity under bounded returns.",
            preamble_names=["bellman_operator"],
            tactic_sequence=["intro v w hvw", "simpa", "exact hvw"],
            stage_outcomes={"formalizer": "ok", "prover": "verified"},
            failure_class=None,
            repair_count=0,
            outcome="verified",
            formalizer_model="mistralai/Leanstral-2603",
            timestamp="2026-04-19T11:00:00+00:00",
        )
    )
    return store


def test_planner_builds_packet_for_bellman_claim(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    driver = FakePlannerDriver(
        {
            "clarifying_questions": [],
            "textbook_defaults": [
                "Assume a bounded and continuous return function with discount factor $\\beta \\in (0,1)$ as in Stokey-Lucas-Prescott."
            ],
            "plan_paragraph": (
                "Use the dynamic-programming Preamble to define the Bellman operator, establish the discounted sup-norm bound "
                "$\\|Tv - Tw\\|_\\infty \\leq \\beta \\|v-w\\|_\\infty$, invoke the contraction mapping result, and package the argument "
                "into Lean-ready subgoals that isolate the operator, the metric estimate, and the fixed-point conclusion."
            ),
            "subgoals": [
                "theorem planner_bellman_subgoal_1 : True := by\n  sorry",
                "theorem planner_bellman_subgoal_2 : True := by\n  sorry",
                "theorem planner_bellman_subgoal_3 : True := by\n  sorry",
            ],
            "needs_review": False,
            "confidence": 0.91,
        }
    )

    service = PlannerService(driver=driver, retrieval_service=retrieval)
    packet = service.build_plan("Prove that the Bellman operator is a contraction")

    selected_names = {hit.name for hit in packet.selected_preamble}
    assert "bellman_operator" in selected_names
    assert "contraction_mapping" in selected_names
    assert any("beta" in default.lower() or "\\beta" in default for default in packet.textbook_defaults)
    assert packet.plan_paragraph
    assert 4 <= len(packet.subgoals) <= 6
    assert packet.needs_review is True
    assert 0.0 <= packet.confidence <= 1.0
    assert 1 <= len(packet.few_shot_traces) <= 2
    assert packet.few_shot_traces[0].shared_preamble_names
    assert any("BellmanOperator" in subgoal for subgoal in packet.subgoals)


def test_planner_upgrades_generic_subgoals_to_specific_targets(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    driver = FakePlannerDriver(
        {
            "clarifying_questions": [],
            "textbook_defaults": [
                "Assume a bounded and continuous return function with discount factor $\\beta \\in (0,1)$ as in Stokey-Lucas-Prescott."
            ],
            "plan_paragraph": (
                "Use the dynamic-programming Preamble to define the Bellman operator, derive the key discounted inequality "
                "$\\|Tv-Tw\\|_\\infty \\leq \\beta \\|v-w\\|_\\infty$, and conclude with a fixed-point/value-function step."
            ),
            "subgoals": [
                "theorem planner_placeholder_1 : True := by\n  sorry",
                "theorem planner_placeholder_2 : True := by\n  sorry",
                "theorem planner_placeholder_3 : True := by\n  sorry",
            ],
            "needs_review": False,
            "confidence": 0.88,
        }
    )

    packet = PlannerService(driver=driver, retrieval_service=retrieval).build_plan(
        "Prove that the Bellman operator is a contraction"
    )

    assert all(": True := by" not in subgoal for subgoal in packet.subgoals)
    assert any("IsContraction" in subgoal for subgoal in packet.subgoals)
    assert any("Function.IsFixedPt" in subgoal for subgoal in packet.subgoals)
    assert any("ValueFunction" in subgoal for subgoal in packet.subgoals)


def test_planner_ambiguous_claim_triggers_questions(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    driver = FakePlannerDriver(
        {
            "clarifying_questions": [
                "Which equilibrium concept is intended: pure-strategy Nash equilibrium or a fixed-point existence statement?",
                "Should the strategy spaces be assumed finite or compact and convex?",
            ],
            "textbook_defaults": [
                "Unless you specify otherwise, adopt textbook continuity and compactness assumptions from MWG for existence-style claims."
            ],
            "plan_paragraph": (
                "First disambiguate the target equilibrium concept and admissible strategy spaces, then map the claim to the closest "
                "Preamble existence result, fix the needed continuity/compactness hypotheses, and express the formalization route "
                "through a small chain of Lean subgoals that isolate hypotheses, existence, and the final economic conclusion in $X$."
            ),
            "subgoals": [
                "theorem planner_equilibrium_subgoal_1 : True := by\n  sorry",
                "theorem planner_equilibrium_subgoal_2 : True := by\n  sorry",
                "theorem planner_equilibrium_subgoal_3 : True := by\n  sorry",
            ],
            "needs_review": True,
            "confidence": 0.63,
        }
    )

    service = PlannerService(driver=driver, retrieval_service=retrieval)
    packet = service.build_plan("Show equilibrium exists")

    assert 1 <= len(packet.clarifying_questions) <= 3
    assert packet.needs_review is True
    assert packet.review_state == "awaiting_plan_review"
    assert packet.clarifying_questions[0].endswith("?")
    assert 0.0 <= packet.confidence <= 1.0


def test_planner_json_output_validation() -> None:
    valid_payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Use MWG continuity assumptions and $\\beta \\in (0,1)$."],
        "plan_paragraph": "Interpret the claim in the discounted setting, derive the key estimate $\\|Tv-Tw\\| \\leq \\beta\\|v-w\\|$, and pass the result to Lean through three theorem-shaped subgoals.",
        "subgoals": [
            "theorem planner_validation_1 {S : Type*} (reward : S → ℝ) (transition : S → S) (β : ℝ) : ∃ T : (S → ℝ) → (S → ℝ), T = BellmanOperator reward transition β := by\n  sorry",
            "theorem planner_validation_2 {V : Type*} [MetricSpace V] (T : V → V) : IsContraction T := by\n  sorry",
            "theorem planner_validation_3 {V : Type*} [MetricSpace V] [CompleteSpace V] [Nonempty V] {K : NNReal} {T : V → V} (hT : ContractingWith K T) : ∃ x, Function.IsFixedPt T x := by\n  sorry",
        ],
        "needs_review": True,
        "confidence": 0.74,
    }
    validated = PlannerLLMResponse.model_validate(valid_payload)
    assert validated.plan_paragraph.startswith("Interpret the claim")

    with pytest.raises(Exception):
        PlannerLLMResponse.model_validate({**valid_payload, "extra_field": "nope"})

    with pytest.raises(Exception):
        PlannerLLMResponse.model_validate(
            {
                **valid_payload,
                "clarifying_questions": ["q1", "q2", "q3", "q4"],
            }
        )

    with pytest.raises(Exception):
        PlannerLLMResponse.model_validate(
            {
                **valid_payload,
                "subgoals": ["theorem only_one : True := by\n  sorry"],
            }
        )

    with pytest.raises(Exception):
        PlannerLLMResponse.model_validate(
            {
                **valid_payload,
                "confidence": 1.4,
            }
        )


def test_hf_planner_driver_uses_chat_completion_and_normalizes_legacy_provider(monkeypatch) -> None:
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Assume standard benchmark conditions."],
        "plan_paragraph": "Map the claim to the Bellman operator preamble and emit Lean-shaped subgoals around $x = x$.",
        "subgoals": [
            "theorem planner_chat_1 : True := by\n  sorry",
            "theorem planner_chat_2 : True := by\n  sorry",
            "theorem planner_chat_3 : True := by\n  sorry",
        ],
        "needs_review": False,
        "confidence": 0.9,
    }
    captured: dict[str, object] = {}

    class FakeInferenceClient:
        def __init__(self, *, model: str, token: str, timeout: float, provider: str) -> None:
            captured["model"] = model
            captured["provider"] = provider

        def chat_completion(self, messages, max_tokens: int, temperature: float):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            captured["temperature"] = temperature
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=111, completion_tokens=37),
            )

    monkeypatch.setattr("huggingface_hub.InferenceClient", FakeInferenceClient)

    backend = PlannerBackend(
        name="minimax-m2.7",
        model="MiniMaxAI/MiniMax-M2.7",
        provider="huggingface",
        notes="test backend",
    )
    response, metadata = HuggingFacePlannerDriver(provider="huggingface").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: 1 + 1 = 2",
    )

    assert response.plan_paragraph.startswith("Map the claim")
    assert captured["provider"] == "auto"
    assert metadata is not None
    assert metadata.input_tokens == 111
    assert metadata.output_tokens == 37
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
