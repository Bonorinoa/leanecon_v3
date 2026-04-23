from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path
from urllib import error as urllib_error

import pytest

from src.memory.models import ProofTrace
from src.memory.store import ProofTraceStore
from src.observability.errors import StageExecutionError
from src.observability.models import ProviderCallMetadata
from src.planner import (
    HuggingFacePlannerDriver,
    MistralPlannerDriver,
    OllamaPlannerDriver,
    PlannerBackend,
    PlannerLLMResponse,
    PlannerService,
)
from src.planner.planner import PlannerConnectivityError, PlannerDriverError
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
    assert 1 <= len(packet.subgoals) <= 6
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
    assert len(packet.subgoals) == 1
    assert "BellmanOperator.monotone" in packet.subgoals[0]


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

    one_subgoal = PlannerLLMResponse.model_validate(
        {
            **valid_payload,
            "subgoals": ["theorem only_one : True := by\n  sorry"],
        }
    )
    assert one_subgoal.subgoals == ["theorem only_one : True := by\n  sorry"]

    with pytest.raises(Exception):
        PlannerLLMResponse.model_validate(
            {
                **valid_payload,
                "confidence": 1.4,
            }
        )


def test_planner_accepts_non_latex_plan_paragraph() -> None:
    validated = PlannerLLMResponse.model_validate(
        {
            "clarifying_questions": [],
            "textbook_defaults": ["Use the direct preamble lemma."],
            "plan_paragraph": "Close the claim by reusing the retrieved preamble lemma and keeping the theorem stub unchanged.",
            "subgoals": ["theorem planner_validation_plain : True := by\n  sorry"],
            "needs_review": False,
            "confidence": 0.74,
        }
    )

    assert validated.plan_paragraph.startswith("Close the claim")


def test_planner_repairs_schema_invalid_locally_without_remote_retry(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    raw_text = """```json
{
  "plan_paragraph": "Reuse the retrieved measure lemma and close the theorem stub directly.",
  "textbook_defaults": {"measure": "standard economic measure"},
  "subgoals": [{"statement": "exact economicMeasure_empty (μ := μ)"}],
  "needs_review": false,
  "confidence": 1.0
}
```"""

    class FlakyDriver:
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, **_: object) -> PlannerLLMResponse:
            self.call_count += 1
            error = PlannerDriverError("Planner backend returned schema-invalid JSON: missing clarifying_questions")
            setattr(
                error,
                "provider_metadata",
                ProviderCallMetadata(response_text=raw_text, raw_planner_response=raw_text),
            )
            raise error

    driver = FlakyDriver()
    result = PlannerService(driver=driver, retrieval_service=retrieval).build_plan_with_telemetry(
        "Under an economic measure, the impossible event has zero mass.",
        theorem_stub=(
            "import Mathlib\n"
            "theorem benchmark_measure_empty {α : Type*} [MeasurableSpace α] (μ : MeasureTheory.Measure α) :\n"
            "    μ ∅ = 0 := by\n"
            "  sorry\n"
        ),
        preamble_names=["measure"],
        benchmark_mode=True,
    )

    assert driver.call_count == 1
    assert result.payload.textbook_defaults == ["measure: standard economic measure"]
    assert result.payload.subgoals[0].startswith("theorem benchmark_measure_empty")
    assert result.usage.error_code == "schema_invalid"


def test_planner_does_not_retry_on_unknown_errors(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )

    class HardFailDriver:
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, **_: object) -> PlannerLLMResponse:
            self.call_count += 1
            raise RuntimeError("unexpected non-retryable failure")

    driver = HardFailDriver()
    service = PlannerService(driver=driver, retrieval_service=retrieval)

    with pytest.raises(RuntimeError, match="unexpected non-retryable failure"):
        service.build_plan("Prove that the Bellman operator is a contraction")

    assert driver.call_count == 1


def test_planner_repairs_schema_invalid_and_persists_raw_response(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    raw_text = json.dumps(
        {
            "plan_paragraph": "Use the measure preamble to close the empty-event identity $\\mu(\\emptyset)=0$ directly.",
            "subgoals": ["exact benchmark_measure_empty"],
        }
    )

    class RepairableDriver:
        def generate(self, **_: object) -> PlannerLLMResponse:
            error = PlannerDriverError("Planner backend returned schema-invalid JSON: missing required keys")
            setattr(
                error,
                "provider_metadata",
                ProviderCallMetadata(response_text=raw_text, raw_planner_response=raw_text),
            )
            raise error

    result = PlannerService(driver=RepairableDriver(), retrieval_service=retrieval).build_plan_with_telemetry(
        "Under an economic measure, the impossible event has zero mass.",
        benchmark_mode=True,
    )

    assert result.payload.clarifying_questions == []
    assert result.payload.textbook_defaults == [
        "Standard PhD-level assumptions (MWG/SLP continuous/bounded return, β∈(0,1), complete metric spaces)"
    ]
    assert result.payload.needs_review is True
    assert result.payload.confidence == 0.65
    assert result.usage.error_code == "schema_invalid"
    assert any(event.raw_planner_response == raw_text for event in result.audit_events if event.error_code == "schema_invalid")


def test_planner_user_prompt_includes_authoritative_theorem_stub(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Assume MWG continuity."],
        "plan_paragraph": (
            "Close the stub via the retrieved preamble lemma and cite the named entry so that the Lean target matches the "
            "stub exactly, with no fabricated $\\beta$ assumptions."
        ),
        "subgoals": [
            "theorem planner_stub_1 {α : Type*} [MeasurableSpace α] (μ : MeasureTheory.Measure α) : μ ∅ = 0 := by\n  sorry",
        ],
        "needs_review": False,
        "confidence": 0.9,
    }

    class CapturingDriver:
        def __init__(self) -> None:
            self.captured: dict[str, object] = {}

        def generate(self, **kwargs: object) -> PlannerLLMResponse:
            self.captured.update(kwargs)
            return PlannerLLMResponse.model_validate(payload)

    driver = CapturingDriver()
    service = PlannerService(driver=driver, retrieval_service=retrieval)

    stub = (
        "import Mathlib\n"
        "open Classical\n\n"
        "theorem benchmark_measure_empty {α : Type*} [MeasurableSpace α] (μ : MeasureTheory.Measure α) :\n"
        "    μ ∅ = 0 := by sorry"
    )
    preamble_names = ["measure"]
    packet = service.build_plan(
        "Under an economic measure, the impossible event has zero mass.",
        theorem_stub=stub,
        preamble_names=preamble_names,
        benchmark_mode=True,
    )

    user_prompt = driver.captured.get("user_prompt")
    assert isinstance(user_prompt, str)
    assert "Authoritative Lean 4 theorem stub" in user_prompt
    assert "Benchmark mode constraints" in user_prompt
    assert "benchmark_measure_empty" in user_prompt
    assert "μ ∅ = 0" in user_prompt
    assert "Pinned preamble entries" in user_prompt
    assert "measure" in user_prompt
    assert packet.review_state == "approved"


@pytest.mark.parametrize(
    ("raw_text", "claim", "theorem_stub", "preamble_names", "expected_theorem"),
    [
        (
            json.dumps(
                {
                    "textbook_defaults": [
                        "The Bellman operator is defined on continuation values with a nonnegative discount factor."
                    ],
                    "plan_paragraph": "Apply the Bellman monotonicity lemma directly.",
                    "subgoals": [
                        "Apply `BellmanOperator.monotone` to show pointwise order preservation."
                    ],
                    "needs_review": False,
                    "confidence": 1.0,
                }
            ),
            "If one continuation-value function dominates another and β is nonnegative, Bellman operator monotonicity preserves that ranking state by state.",
            (
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator\n\n"
                "theorem benchmark_bellman_operator_monotone\n"
                "    {S : Type*} {reward : S → ℝ} {transition : S → S} {β : ℝ}\n"
                "    (hβ : 0 ≤ β) {v w : S → ℝ} (hvw : ∀ s, v s ≤ w s) :\n"
                "    ∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s := by\n"
                "  sorry\n"
            ),
            ["bellman_operator"],
            "benchmark_bellman_operator_monotone",
        ),
        (
            json.dumps(
                {
                    "textbook_defaults": [
                        "The objective ordering is represented by the codomain order on the objective function."
                    ],
                    "plan_paragraph": "Use the constrained maximum certificate directly.",
                    "subgoals": [
                        "Apply `IsConstrainedMaximum.value_le` to compare the maximizer against any feasible alternative."
                    ],
                    "needs_review": False,
                    "confidence": 1.0,
                }
            ),
            "A constrained maximizer weakly dominates every feasible alternative in the objective ordering.",
            (
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.Optimization.ConstrainedOptimization\n\n"
                "theorem benchmark_constrained_maximum_value_le\n"
                "    {α : Type*} {f : α → ℝ} {feasible : Set α} {x y : α}\n"
                "    (hx : IsConstrainedMaximum f feasible x) (hy : y ∈ feasible) :\n"
                "    f y ≤ f x := by\n"
                "  sorry\n"
            ),
            ["constrained_optimization"],
            "benchmark_constrained_maximum_value_le",
        ),
        (
            json.dumps(
                {
                    "textbook_defaults": [
                        "The operator acts on a complete metric space with a contraction constant in (0, 1)."
                    ],
                    "plan_paragraph": "Use the contraction-mapping template to obtain the fixed point.",
                    "subgoals": [
                        "Apply `contraction_has_fixedPoint` to the Bellman-style operator."
                    ],
                    "needs_review": False,
                    "confidence": 1.0,
                }
            ),
            "A Bellman-style operator satisfying contraction conditions fits the contraction-mapping template used in recursive economics.",
            (
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping\n\n"
                "theorem benchmark_contraction_template\n"
                "    {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α]\n"
                "    {K : NNReal} {f : α → α} (hf : ContractingWith K f) :\n"
                "    ∃ x, Function.IsFixedPt f x := by\n"
                "  sorry\n"
            ),
            ["contraction_mapping"],
            "benchmark_contraction_template",
        ),
        (
            json.dumps(
                {
                    "textbook_defaults": [
                        "Policy improvement is defined by the underlying evaluation order."
                    ],
                    "plan_paragraph": "Use reflexivity of the policy-improvement relation.",
                    "subgoals": [
                        "Apply `policyImproves_refl` or `le_rfl`."
                    ],
                    "needs_review": False,
                    "confidence": 1.0,
                }
            ),
            "Policy improvement is reflexive for any evaluation criterion, providing a base case for policy-iteration arguments.",
            (
                "import Mathlib\n"
                "import LeanEcon.Preamble.Foundations.DynamicProgramming.PolicyIteration\n\n"
                "theorem benchmark_policy_self_improves\n"
                "    {π : Type*} (criterion : π → ℝ) (policy : π) :\n"
                "    PolicyImproves criterion policy policy := by\n"
                "  sorry\n"
            ),
            ["policy_iteration"],
            "benchmark_policy_self_improves",
        ),
    ],
)
def test_planner_repairs_frontier_schema_invalid_payloads_to_authoritative_stub(
    tmp_path: Path,
    raw_text: str,
    claim: str,
    theorem_stub: str,
    preamble_names: list[str],
    expected_theorem: str,
) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )

    class RepairableDriver:
        def generate(self, **_: object) -> PlannerLLMResponse:
            error = PlannerDriverError("Planner backend returned schema-invalid JSON: missing required keys")
            setattr(
                error,
                "provider_metadata",
                ProviderCallMetadata(response_text=raw_text, raw_planner_response=raw_text),
            )
            raise error

    result = PlannerService(driver=RepairableDriver(), retrieval_service=retrieval).build_plan_with_telemetry(
        claim,
        theorem_stub=theorem_stub,
        preamble_names=preamble_names,
        benchmark_mode=True,
    )

    assert result.payload.subgoals[0].startswith(f"theorem {expected_theorem}")
    assert result.usage.error_code == "schema_invalid"


def test_planner_benchmark_mode_falls_back_when_subgoal_uses_wrong_theorem_name(tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    driver = FakePlannerDriver(
        {
            "clarifying_questions": [],
            "textbook_defaults": ["Assume the direct preamble lemma closes the goal."],
            "plan_paragraph": "Close the benchmark claim directly.",
            "subgoals": [
                "theorem planner_wrong_name : True := by\n  sorry",
            ],
            "needs_review": False,
            "confidence": 0.92,
        }
    )

    packet = PlannerService(driver=driver, retrieval_service=retrieval).build_plan(
        "Under an economic measure, the impossible event has zero mass.",
        theorem_stub=(
            "import Mathlib\n"
            "theorem benchmark_measure_empty {α : Type*} [MeasurableSpace α] (μ : MeasureTheory.Measure α) :\n"
            "    μ ∅ = 0 := by\n"
            "  sorry\n"
        ),
        preamble_names=["measure"],
        benchmark_mode=True,
    )

    assert packet.subgoals[0].startswith("theorem benchmark_measure_empty")


def test_planner_context_uses_pinned_preamble_and_filters_irrelevant_memory(tmp_path: Path) -> None:
    store = ProofTraceStore(tmp_path / "planner-memory.db")
    store.record(
        ProofTrace(
            claim_id="trace-irrelevant",
            claim_text="1 + 1 = 2",
            preamble_names=["measure"],
            tactic_sequence=["simp"],
            stage_outcomes={"formalizer": "ok", "prover": "verified"},
            failure_class=None,
            repair_count=0,
            outcome="verified",
            formalizer_model="mistralai/Leanstral-2603",
            timestamp="2026-04-19T12:00:00+00:00",
        )
    )
    store.record(
        ProofTrace(
            claim_id="trace-relevant",
            claim_text="Under an economic measure, the empty event receives zero mass.",
            preamble_names=["measure"],
            tactic_sequence=["simpa using economicMeasure_empty"],
            stage_outcomes={"formalizer": "ok", "prover": "verified"},
            failure_class=None,
            repair_count=0,
            outcome="verified",
            formalizer_model="mistralai/Leanstral-2603",
            timestamp="2026-04-19T13:00:00+00:00",
        )
    )
    retrieval = PlannerRetrievalService(embedder=HashingTextEmbedder(), trace_store=store)

    context = retrieval.build_context(
        "Under an economic measure, the impossible event has zero mass.",
        theorem_stub=(
            "import Mathlib\n"
            "theorem benchmark_measure_empty {α : Type*} [MeasurableSpace α] (μ : MeasureTheory.Measure α) :\n"
            "    μ ∅ = 0 := by\n"
            "  sorry\n"
        ),
        preamble_names=["measure"],
    )

    assert [hit.name for hit in context.selected_preamble][:1] == ["measure"]
    assert len(context.selected_preamble) <= 2
    assert len(context.few_shot_traces) == 1
    assert context.few_shot_traces[0].claim_text != "1 + 1 = 2"
    assert "economicMeasure_empty" in context.preamble_context


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

        def chat_completion(self, messages, max_tokens: int, temperature: float, response_format):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            captured["temperature"] = temperature
            captured["response_format"] = response_format
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=111, completion_tokens=37),
            )

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(InferenceClient=FakeInferenceClient))

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
    assert captured["max_tokens"] == 500
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert captured["response_format"]["type"] == "json_schema"


def test_hf_planner_driver_normalizes_fenced_json_and_object_fields(monkeypatch) -> None:
    class FakeInferenceClient:
        def __init__(self, *, model: str, token: str, timeout: float, provider: str) -> None:
            pass

        def chat_completion(self, messages, max_tokens: int, temperature: float, response_format):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="""```json
{
  "plan_paragraph": "Close the claim by reusing the direct preamble lemma.",
  "textbook_defaults": {"measure": "economic measure"},
  "subgoals": [{"statement": "exact economicMeasure_empty (μ := μ)"}],
  "needs_review": false,
  "confidence": 1.0
}
```"""
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
            )

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(InferenceClient=FakeInferenceClient))

    backend = PlannerBackend(
        name="hf-structured",
        model="Qwen/Qwen3-32B",
        provider="auto",
        notes="test backend",
    )
    response, _ = HuggingFacePlannerDriver(provider="auto").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: impossible event has zero mass",
    )

    assert response.textbook_defaults == ["measure: economic measure"]
    assert response.subgoals == ["exact economicMeasure_empty (μ := μ)"]


def test_mistral_planner_driver_posts_structured_chat_request(monkeypatch) -> None:
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Assume standard benchmark conditions."],
        "plan_paragraph": "Use the direct algebraic identity and preserve the theorem stub shape.",
        "subgoals": [
            "exact two_mul (1 : ℕ)",
        ],
        "needs_review": False,
        "confidence": 0.92,
    }
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(payload),
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 41,
                        "completion_tokens": 19,
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="mistral-structured",
        model="mistral-large-2512",
        provider="mistral",
        notes="test backend",
    )
    response, metadata = MistralPlannerDriver(api_key="mistral_test", base_url="https://api.mistral.ai/v1").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: 1 + 1 = 2",
    )

    assert response.plan_paragraph.startswith("Use the direct algebraic identity")
    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["authorization"] == "Bearer mistral_test"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"]["model"] == "mistral-large-2512"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert metadata is not None
    assert metadata.input_tokens == 41
    assert metadata.output_tokens == 19


def test_ollama_planner_driver_posts_chat_schema(monkeypatch) -> None:
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Assume standard benchmark conditions."],
        "plan_paragraph": "Use the retrieved measure lemma to close the stub directly and preserve the target $\\mu(\\emptyset)=0$.",
        "subgoals": [
            "exact economicMeasure_empty (μ := μ)",
        ],
        "needs_review": False,
        "confidence": 1.0,
    }
    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, payload_obj: dict[str, object]) -> None:
            self.payload_obj = payload_obj

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload_obj).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        if request.full_url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "gemma4:31b"}]})
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "message": {"role": "assistant", "content": json.dumps(payload)},
                "prompt_eval_count": 88,
                "eval_count": 17,
                "done_reason": "stop",
            }
        )

    monkeypatch.setattr("src.planner.planner.PLANNER_MODEL", "gemma4:31b")
    monkeypatch.setattr("src.planner.planner.LIVE_MODEL_TESTS_ENABLED", False)
    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="ollama-cloud",
        model="gemma4:31b",
        provider="ollama",
        notes="test backend",
    )
    response, metadata = OllamaPlannerDriver(api_key="ollama_test", host="https://ollama.com").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: impossible event has zero mass",
    )

    assert response.plan_paragraph.startswith("Use the retrieved measure lemma")
    assert captured["url"] == "https://ollama.com/api/chat"
    assert captured["authorization"] == "Bearer ollama_test"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"]["model"] == "gemma4:31b"
    assert captured["body"]["stream"] is False
    assert captured["body"]["format"]["type"] == "object"
    assert metadata is not None
    assert metadata.input_tokens == 88
    assert metadata.output_tokens == 17


def test_ollama_planner_driver_surfaces_connectivity_http_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float):
        raise urllib_error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("src.planner.planner.LIVE_MODEL_TESTS_ENABLED", False)
    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="ollama-cloud",
        model="gemma4:31b",
        provider="ollama",
        notes="test backend",
    )

    with pytest.raises(PlannerConnectivityError):
        OllamaPlannerDriver(api_key="ollama_test", host="https://ollama.com").generate(
            backend=backend,
            system_prompt="Return only JSON.",
            user_prompt="Claim: 1 + 1 = 2",
        )


def test_ollama_planner_driver_omits_auth_for_local_host(monkeypatch) -> None:
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": ["Assume standard benchmark conditions."],
        "plan_paragraph": "Close the target via the retrieved measure lemma and preserve $\\mu(\\emptyset)=0$.",
        "subgoals": [
            "exact economicMeasure_empty (μ := μ)",
        ],
        "needs_review": False,
        "confidence": 1.0,
    }
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                    "prompt_eval_count": 88,
                    "eval_count": 17,
                    "done_reason": "stop",
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="ollama-cloud",
        model="gemma4:31b-cloud",
        provider="ollama",
        notes="test backend",
    )
    OllamaPlannerDriver(api_key="ollama_test", host="http://127.0.0.1:11434").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: impossible event has zero mass",
    )

    assert captured["authorization"] is None


def test_planner_fast_fails_when_local_ollama_endpoint_is_unreachable(monkeypatch, tmp_path: Path) -> None:
    retrieval = PlannerRetrievalService(
        embedder=HashingTextEmbedder(),
        trace_store=_make_trace_store(tmp_path),
    )
    urlopen_calls: list[tuple[str, float]] = []
    sleep_calls: list[float] = []

    def fake_urlopen(request, timeout: float):
        urlopen_calls.append((request.full_url, timeout))
        raise urllib_error.URLError("connect: operation not permitted")

    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)
    monkeypatch.setattr("src.planner.planner.time.sleep", lambda seconds: sleep_calls.append(seconds))

    service = PlannerService(
        backend="ollama-cloud",
        driver=OllamaPlannerDriver(api_key="ollama_test", host="http://127.0.0.1:11434", timeout=360),
        retrieval_service=retrieval,
    )

    with pytest.raises(StageExecutionError) as exc_info:
        service.build_plan_with_telemetry("Show the empty event has zero measure.")

    assert exc_info.value.error_code == "provider_unavailable"
    assert "local Ollama planner endpoint unreachable" in exc_info.value.message
    assert urlopen_calls == [("http://127.0.0.1:11434/api/tags", 1.0)]
    assert sleep_calls == []


def test_hosted_ollama_connectivity_checks_tags_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"models": [{"name": "gemma4:31b"}]}).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("src.planner.planner.PLANNER_MODEL", "gemma4:31b")
    monkeypatch.setattr("src.planner.planner.LIVE_MODEL_TESTS_ENABLED", False)
    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    driver = OllamaPlannerDriver(api_key="ollama_test", host="https://ollama.com", timeout=360)
    ok, reason = driver.connectivity_status()

    assert ok is True
    assert reason is None
    assert captured["url"] == "https://ollama.com/api/tags"
    assert captured["authorization"] == "Bearer ollama_test"
    assert captured["timeout"] == 5.0


def test_hosted_ollama_connectivity_uses_live_probe_when_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        raise urllib_error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("src.planner.planner.PLANNER_MODEL", "gemma4:31b")
    monkeypatch.setattr("src.planner.planner.LIVE_MODEL_TESTS_ENABLED", True)
    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    driver = OllamaPlannerDriver(api_key="ollama_test", host="https://ollama.com", timeout=360)
    ok, reason = driver.connectivity_status()

    assert ok is False
    assert "HTTP 401" in str(reason)
    assert captured["url"] == "https://ollama.com/api/chat"
    assert captured["authorization"] == "Bearer ollama_test"
    assert captured["body"]["model"] == "gemma4:31b"
    assert captured["body"]["stream"] is False
    assert captured["timeout"] == 15.0


def test_ollama_planner_driver_backfills_empty_textbook_defaults(monkeypatch) -> None:
    payload = {
        "clarifying_questions": [],
        "textbook_defaults": [],
        "plan_paragraph": "Close the target via the retrieved measure lemma and preserve $\\mu(\\emptyset)=0$.",
        "subgoals": [
            "exact economicMeasure_empty (μ := μ)",
        ],
        "needs_review": False,
        "confidence": 1.0,
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                    "prompt_eval_count": 88,
                    "eval_count": 17,
                    "done_reason": "stop",
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        return FakeResponse()

    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="ollama-cloud",
        model="gemma4:31b-cloud",
        provider="ollama",
        notes="test backend",
    )
    response, _ = OllamaPlannerDriver(api_key="ollama_test", host="http://127.0.0.1:11434").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: impossible event has zero mass",
    )

    assert response.textbook_defaults == [
        "Standard PhD-level assumptions (MWG/SLP continuous/bounded return, β∈(0,1), complete metric spaces)"
    ]


def test_ollama_planner_driver_backfills_missing_required_fields(monkeypatch) -> None:
    payload = {
        "plan_paragraph": "The restricted utility map preserves continuity on the subset $s \\subseteq \\alpha$.",
        "subgoals": [
            "exact hu.continuousOn s",
        ],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                    "prompt_eval_count": 88,
                    "eval_count": 17,
                    "done_reason": "stop",
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: float):
        return FakeResponse()

    monkeypatch.setattr("src.planner.planner.urllib_request.urlopen", fake_urlopen)

    backend = PlannerBackend(
        name="ollama-cloud",
        model="gemma4:31b-cloud",
        provider="ollama",
        notes="test backend",
    )
    response, _ = OllamaPlannerDriver(api_key="ollama_test", host="http://127.0.0.1:11434").generate(
        backend=backend,
        system_prompt="Return only JSON.",
        user_prompt="Claim: continuity on a subset",
    )

    assert response.clarifying_questions == []
    assert response.needs_review is False
    assert response.confidence == 0.75
