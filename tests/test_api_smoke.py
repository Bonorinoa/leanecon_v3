from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app


def test_plan_formalize_and_job_smoke() -> None:
    client = TestClient(app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    assert plan.json()["status"] == "completed"

    formalize = client.post("/formalize", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert formalize.status_code == 200
    assert formalize.json()["result"]["benchmark_mode"] is True


def test_verify_job_lifecycle() -> None:
    client = TestClient(app)

    verify = client.post("/verify", json={"theorem_with_sorry": "theorem demo : True := by\n  sorry\n"})
    assert verify.status_code == 200
    job_id = verify.json()["job_id"]

    poll = client.get(f"/jobs/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] in {"queued", "running_prover", "completed", "failed"}


def test_health_and_metrics() -> None:
    client = TestClient(app)

    health = client.get("/health")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
