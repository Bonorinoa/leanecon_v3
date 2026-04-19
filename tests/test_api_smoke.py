from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from src.planner import PlannerLLMResponse, PlannerService


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


def test_plan_formalize_and_job_smoke(monkeypatch) -> None:
    api_module = importlib.import_module("src.api.app")
    monkeypatch.setattr(api_module, "planner", PlannerService(driver=FakePlannerDriver()))
    client = TestClient(api_module.app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    assert plan.json()["status"] == "completed"
    assert plan.json()["result"]["plan_paragraph"]
    assert plan.json()["result"]["needs_review"] is False
    assert 0.0 <= plan.json()["result"]["confidence"] <= 1.0
    assert any("BellmanOperator" in subgoal for subgoal in plan.json()["result"]["subgoals"])

    formalize = client.post("/formalize", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert formalize.status_code == 200
    assert formalize.json()["result"]["benchmark_mode"] is True


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
