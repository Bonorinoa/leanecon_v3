from __future__ import annotations

from src.formalizer import FormalizerGenerationResponse, FormalizerService, FormalizerSubgoal
from src.planner import PlannerPacket


def _planner_packet() -> PlannerPacket:
    return PlannerPacket.model_validate(
        {
            "claim": "Prove that the Bellman operator is a contraction on discounted value functions.",
            "clarifying_questions": [],
            "textbook_defaults": [
                "Assume discounted dynamic programming with bounded returns and $\\beta \\in (0,1)$."
            ],
            "plan_paragraph": (
                "Use the Bellman operator and contraction-mapping Preamble entries to isolate the Bellman estimate, "
                "then connect it to the fixed-point value-function conclusion with the Lean-ready estimate "
                "$\\|Tv-Tw\\| \\leq \\beta \\|v-w\\|$ encoded as local subgoals."
            ),
            "subgoals": [
                "theorem bellman_stub_1 : True := by\n  sorry",
                "theorem bellman_stub_2 : True := by\n  sorry",
                "theorem bellman_stub_3 : True := by\n  sorry",
                "theorem bellman_stub_4 : True := by\n  sorry",
            ],
            "needs_review": False,
            "confidence": 0.92,
            "review_state": "approved",
            "backend": "minimax-m2.7",
            "model": "MiniMaxAI/MiniMax-M2.7",
            "selected_preamble": [
                {
                    "name": "bellman_operator",
                    "lean_module": "LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator",
                    "score": 9.0,
                    "description": "Deterministic Bellman operator with monotonicity lemma.",
                    "concepts": ["bellman_operator", "dynamic_programming", "monotone_operator"],
                    "proven_lemmas": ["BellmanOperator.monotone"],
                    "tactic_hints": ["simpa using add_le_add_left hmul (reward s)"],
                    "textbook_source": "SLP Ch. 4",
                    "related": ["contraction_mapping", "value_function"],
                },
                {
                    "name": "contraction_mapping",
                    "lean_module": "LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping",
                    "score": 8.0,
                    "description": "Global contractions and fixed-point existence.",
                    "concepts": ["contraction_mapping", "fixed_point"],
                    "proven_lemmas": ["contraction_has_fixedPoint"],
                    "tactic_hints": ["rcases hf with ⟨K, hK⟩"],
                    "textbook_source": "SLP Ch. 4",
                    "related": ["bellman_operator", "value_function"],
                },
                {
                    "name": "value_function",
                    "lean_module": "LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction",
                    "score": 7.0,
                    "description": "Fixed-point value functions for contracting dynamic problems.",
                    "concepts": ["value_function", "fixed_point", "bellman_solution"],
                    "proven_lemmas": ["valueFunction_isFixedPt"],
                    "tactic_hints": ["simpa [ValueFunction] using ContractingWith.fixedPoint_isFixedPt (f := T) hT"],
                    "textbook_source": "SLP Ch. 4",
                    "related": ["bellman_operator", "contraction_mapping"],
                },
            ],
            "few_shot_traces": [],
        }
    )


class RevisingMistralFormalizerDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> FormalizerGenerationResponse:
        self.calls.append(kwargs)
        prompt = str(kwargs.get("user_prompt", ""))
        if "Revision request:" not in prompt:
            return FormalizerGenerationResponse(
                theorem_name="bellman_contraction_claim",
                theorem_docstring="Initial Bellman contraction skeleton.",
                theorem_statement="True",
                open_statements=[],
                subgoals=[
                    FormalizerSubgoal(
                        name="h_subgoal_1",
                        statement="True",
                        rationale="Generic placeholder that should trigger revision.",
                    ),
                    FormalizerSubgoal(
                        name="h_subgoal_2",
                        statement="True",
                        rationale="Another placeholder that should be rejected.",
                    ),
                ],
                final_expression=None,
            )

        return FormalizerGenerationResponse(
            theorem_name="bellman_contraction_claim",
            theorem_docstring="Bellman contraction skeleton using explicit Preamble obligations for the prover.",
            theorem_statement=(
                "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
            ),
            open_statements=[],
            subgoals=[
                FormalizerSubgoal(
                    name="h_bellman_monotone",
                    statement=(
                        "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                        "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                        "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
                    ),
                    rationale="Use `BellmanOperator.monotone`; the local proof can follow the hint `simpa using add_le_add_left hmul (reward s)`.",
                ),
                FormalizerSubgoal(
                    name="h_bellman_expand",
                    statement=(
                        "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ) (v : S → ℝ) (s : S), "
                        "BellmanOperator reward transition β v s = reward s + β * v (transition s)"
                    ),
                    rationale="Expose the Bellman operator definition so the prover can unfold the recursive expression explicitly.",
                ),
                FormalizerSubgoal(
                    name="h_contraction_fixed_point",
                    statement=(
                        "∀ {α : Type} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, "
                        "IsContraction f → ∃ x, Function.IsFixedPt f x"
                    ),
                    rationale="Use `contraction_has_fixedPoint`; the tactic hint starts with `rcases hf with ⟨K, hK⟩`.",
                ),
                FormalizerSubgoal(
                    name="h_value_function_fixed",
                    statement=(
                        "∀ {V : Type} [MetricSpace V] [CompleteSpace V] [Nonempty V] {K : NNReal} "
                        "(T : V → V) (hT : ContractingWith K T), Function.IsFixedPt T (ValueFunction T hT)"
                    ),
                    rationale="Use `valueFunction_isFixedPt`; the proof can `simpa [ValueFunction]`.",
                ),
            ],
            final_expression=None,
        )


class RecordingHFFormalizerDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> FormalizerGenerationResponse:
        self.calls.append(kwargs)
        return FormalizerGenerationResponse(
            theorem_name="goedel_bellman_claim",
            theorem_docstring="Goedel-backed Bellman skeleton.",
            theorem_statement=(
                "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
            ),
            open_statements=[],
            subgoals=[
                FormalizerSubgoal(
                    name="h_bellman_monotone",
                    statement=(
                        "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                        "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                        "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
                    ),
                    rationale="Directly package `BellmanOperator.monotone` for the prover.",
                ),
                FormalizerSubgoal(
                    name="h_bellman_expand",
                    statement=(
                        "∀ {S : Type} (reward : S → ℝ) (transition : S → S) (β : ℝ) (v : S → ℝ) (s : S), "
                        "BellmanOperator reward transition β v s = reward s + β * v (transition s)"
                    ),
                    rationale="Expose the Bellman operator definition.",
                ),
                FormalizerSubgoal(
                    name="h_contraction_fixed_point",
                    statement=(
                        "∀ {α : Type} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, "
                        "IsContraction f → ∃ x, Function.IsFixedPt f x"
                    ),
                    rationale="Directly package `contraction_has_fixedPoint` for the prover.",
                ),
                FormalizerSubgoal(
                    name="h_value_function_fixed",
                    statement=(
                        "∀ {V : Type} [MetricSpace V] [CompleteSpace V] [Nonempty V] {K : NNReal} "
                        "(T : V → V) (hT : ContractingWith K T), Function.IsFixedPt T (ValueFunction T hT)"
                    ),
                    rationale="Directly package `valueFunction_isFixedPt` for the prover.",
                ),
            ],
            final_expression=None,
        )


def test_formalizer_revises_generic_output_into_specific_bellman_subgoals() -> None:
    mistral_driver = RevisingMistralFormalizerDriver()
    service = FormalizerService(backend="leanstral", mistral_driver=mistral_driver)
    packet = service.formalize(
        _planner_packet().claim,
        planner_packet=_planner_packet().model_dump(mode="json"),
        benchmark_mode=True,
    )

    assert packet.provider == "mistral"
    assert packet.model == "labs-leanstral-2603"
    assert packet.faithfulness.score >= 4.5
    assert packet.faithfulness.passes_gate is True
    assert packet.parse_check.success is True
    assert len(packet.subgoals) == 4
    assert all(subgoal.statement != "True" for subgoal in packet.subgoals)
    assert any("BellmanOperator" in subgoal.statement for subgoal in packet.subgoals)
    assert any("IsContraction" in subgoal.statement for subgoal in packet.subgoals)
    assert any("ValueFunction" in subgoal.statement for subgoal in packet.subgoals)
    assert "have h_bellman_monotone" in packet.lean_code
    assert packet.lean_code.rstrip().endswith("sorry")
    assert len(mistral_driver.calls) == 2
    assert "Revision request:" in str(mistral_driver.calls[1]["user_prompt"])
    assert "Target 4 to 6 named subgoals for this claim." in str(mistral_driver.calls[0]["user_prompt"])


def test_formalizer_routes_goedel_backend_to_huggingface() -> None:
    mistral_driver = RevisingMistralFormalizerDriver()
    hf_driver = RecordingHFFormalizerDriver()
    service = FormalizerService(
        backend="goedel-prover-v2",
        mistral_driver=mistral_driver,
        huggingface_driver=hf_driver,
    )
    packet = service.formalize(
        _planner_packet().claim,
        planner_packet=_planner_packet().model_dump(mode="json"),
        benchmark_mode=True,
    )

    assert packet.provider == "huggingface"
    assert packet.backend == "goedel-prover-v2"
    assert packet.model == "Goedel-LM/Goedel-Prover-V2-32B"
    assert len(hf_driver.calls) == 1
    assert len(mistral_driver.calls) == 0
