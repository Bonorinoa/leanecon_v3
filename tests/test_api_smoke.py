from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from src.formalizer import FormalizerGenerationResponse, FormalizerService, FormalizerSubgoal
from src.planner import PlannerLLMResponse, PlannerPacket, PlannerService


class FakePlannerDriver:
    def generate(self, **_: object) -> PlannerLLMResponse:
        return PlannerLLMResponse.model_validate(
            {
                "clarifying_questions": [],
                "textbook_defaults": ["Use discounted dynamic-programming defaults with $\\beta \\in (0,1)$."],
                "plan_paragraph": "Interpret the Bellman claim in the discounted setting, derive the key contraction estimate $\\|Tv-Tw\\| \\leq \\beta\\|v-w\\|$, and emit Lean-ready subgoals.",
                "subgoals": [
                    "theorem api_smoke_subgoal_1 : True := by\n  sorry",
                    "theorem api_smoke_subgoal_2 : True := by\n  sorry",
                    "theorem api_smoke_subgoal_3 : True := by\n  sorry",
                ],
                "needs_review": False,
                "confidence": 0.9,
            }
        )


class FakeMistralFormalizerDriver:
    def generate(self, **_: object) -> FormalizerGenerationResponse:
        return FormalizerGenerationResponse(
            theorem_name="bellman_contraction_stub",
            theorem_docstring="Bellman contraction skeleton grounded in the dynamic-programming preamble.",
            theorem_statement=(
                "∀ {S : Type*} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
            ),
            open_statements=[],
            subgoals=[
                FormalizerSubgoal(
                    name="h_subgoal_1",
                    statement=(
                        "∀ {S : Type*} (reward : S → ℝ) (transition : S → S) (β : ℝ), "
                        "0 ≤ β → ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) → "
                        "∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s"
                    ),
                    rationale="Local monotonicity estimate from the Bellman operator preamble theorem.",
                )
            ],
            final_expression=None,
        )


def _planner_packet() -> dict[str, object]:
    packet = PlannerPacket.model_validate(
        {
            "claim": "Prove that the Bellman operator is a contraction on discounted value functions.",
            "clarifying_questions": [],
            "textbook_defaults": [
                "Assume discounted dynamic programming with bounded returns and $\\beta \\in (0,1)$."
            ],
            "plan_paragraph": (
                "Use the Bellman operator and contraction-mapping Preamble entries to state the monotonicity and "
                "discounted fixed-point route, then package the proof as Lean-ready local subgoals around "
                "$\\|Tv-Tw\\| \\leq \\beta\\|v-w\\|$."
            ),
            "subgoals": [
                "theorem api_stub_1 : True := by\n  sorry",
                "theorem api_stub_2 : True := by\n  sorry",
                "theorem api_stub_3 : True := by\n  sorry",
            ],
            "needs_review": False,
            "confidence": 0.88,
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
            ],
            "few_shot_traces": [],
        }
    )
    return packet.model_dump(mode="json")


def test_plan_formalize_and_job_smoke(monkeypatch) -> None:
    api_module = importlib.import_module("src.api.app")
    monkeypatch.setattr(api_module, "planner", PlannerService(driver=FakePlannerDriver()))
    monkeypatch.setattr(
        api_module,
        "formalizer",
        FormalizerService(mistral_driver=FakeMistralFormalizerDriver()),
    )
    client = TestClient(api_module.app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    assert plan.json()["status"] == "completed"
    assert plan.json()["result"]["plan_paragraph"]
    assert plan.json()["result"]["needs_review"] is False

    formalize = client.post(
        "/formalize",
        json={
            "claim": "A Bellman equation claim.",
            "planner_packet": _planner_packet(),
            "benchmark_mode": True,
        },
    )
    assert formalize.status_code == 200
    payload = formalize.json()["result"]
    assert payload["benchmark_mode"] is True
    assert payload["provider"] == "mistral"
    assert payload["model"] == "labs-leanstral-2603"
    assert "lean_code" in payload
    assert "theorem_with_sorry" in payload
    assert "LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator" in payload["imports"]


def test_verify_job_lifecycle() -> None:
    api_module = importlib.import_module("src.api.app")
    client = TestClient(api_module.app)

    verify = client.post("/verify", json={"theorem_with_sorry": "theorem demo : True := by\n  sorry\n"})
    assert verify.status_code == 200
    job_id = verify.json()["job_id"]

    poll = client.get(f"/jobs/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] in {"queued", "running_prover", "completed", "failed"}


def test_health_and_metrics() -> None:
    api_module = importlib.import_module("src.api.app")
    client = TestClient(api_module.app)

    health = client.get("/health")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
