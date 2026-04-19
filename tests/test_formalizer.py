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


class RecordingMistralFormalizerDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> FormalizerGenerationResponse:
        self.calls.append(kwargs)
        return FormalizerGenerationResponse(
            theorem_name="bellman_contraction_claim",
            theorem_docstring="Bellman contraction skeleton using the Bellman operator, contraction mapping, and value-function context.",
            theorem_statement="True",
            open_statements=[],
            subgoals=[
                FormalizerSubgoal(
                    name="h_subgoal_1",
                    statement="True",
                    rationale="Use the Bellman monotonicity lemma from the Preamble.",
                ),
                FormalizerSubgoal(
                    name="h_subgoal_2",
                    statement="True",
                    rationale="Expose the contraction-mapping fixed-point certificate for the Prover.",
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
            theorem_statement="True",
            open_statements=[],
            subgoals=[FormalizerSubgoal(name="h_subgoal_1", statement="True", rationale="Placeholder")],
            final_expression=None,
        )


def test_formalizer_generates_faithful_parsable_bellman_stub() -> None:
    mistral_driver = RecordingMistralFormalizerDriver()
    service = FormalizerService(backend="leanstral", mistral_driver=mistral_driver)
    packet = service.formalize(
        _planner_packet().claim,
        planner_packet=_planner_packet().model_dump(mode="json"),
        benchmark_mode=True,
    )

    assert packet.provider == "mistral"
    assert packet.model == "labs-leanstral-2603"
    assert packet.faithfulness.score >= 4.0
    assert packet.faithfulness.feedback
    assert packet.parse_check.success is True
    assert "LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator" in packet.imports
    assert "LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping" in packet.imports
    assert "LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction" in packet.imports
    assert "import Mathlib" not in packet.lean_code
    assert "have h_subgoal_1" in packet.lean_code
    assert "have h_subgoal_2" in packet.lean_code
    assert len(mistral_driver.calls) == 1


def test_formalizer_routes_goedel_backend_to_huggingface() -> None:
    mistral_driver = RecordingMistralFormalizerDriver()
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
