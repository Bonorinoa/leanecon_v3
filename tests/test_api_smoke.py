from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api.jobs import JobStore
from src.formalizer import FormalizerGenerationResponse, FormalizerService, FormalizerSubgoal
from src.planner import PlannerLLMResponse, PlannerPacket, PlannerService
from src.prover.models import ProverResult


class FakePlannerDriver:
    def generate(self, **_: object) -> PlannerLLMResponse:
        return PlannerLLMResponse.model_validate(
            {
                "clarifying_questions": [],
                "textbook_defaults": ["Use discounted dynamic-programming defaults with $\\beta \\in (0,1)$."],
                "plan_paragraph": "Interpret the Bellman claim in the discounted setting and emit Lean-ready subgoals around $\\|Tv-Tw\\| \\le \\beta\\|v-w\\|$.",
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
            "plan_paragraph": "Use the Bellman operator and contraction-mapping entries to package a Lean proof route around $\\|Tv-Tw\\| \\le \\beta\\|v-w\\|$.",
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
                }
            ],
            "few_shot_traces": [],
        }
    )
    return packet.model_dump(mode="json")


def _configure_api(monkeypatch, tmp_path):
    api_module = importlib.import_module("src.api.app")
    monkeypatch.setattr(api_module, "job_store", JobStore(tmp_path / "jobs.db", ttl_seconds=3600))
    monkeypatch.setattr(api_module, "planner", PlannerService(driver=FakePlannerDriver()))
    monkeypatch.setattr(
        api_module,
        "formalizer",
        FormalizerService(mistral_driver=FakeMistralFormalizerDriver()),
    )
    return api_module


def test_plan_formalize_and_job_smoke(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    client = TestClient(api_module.app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    plan_payload = plan.json()
    assert plan_payload["status"] == "completed"
    assert plan_payload["result"]["plan_paragraph"]
    assert plan_payload["result"]["usage_by_stage"]["planner"]["stage"] == "planner"
    assert plan_payload["result"]["timing_breakdown"]["planner_ms"] >= 0.0

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
    assert payload["usage_by_stage"]["formalizer"]["stage"] == "formalizer"
    assert payload["audit_summary"]["event_count"] >= 1


def test_formalize_accepts_hydrated_plan_result(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    client = TestClient(api_module.app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    hydrated_planner_packet = plan.json()["result"]

    formalize = client.post(
        "/formalize",
        json={
            "claim": "A Bellman equation claim.",
            "planner_packet": hydrated_planner_packet,
            "benchmark_mode": True,
        },
    )

    assert formalize.status_code == 200
    payload = formalize.json()["result"]
    assert payload["theorem_name"] == "formalizer_bellman_contraction_stub"
    assert payload["usage_by_stage"]["formalizer"]["stage"] == "formalizer"


def test_prove_job_lifecycle(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    formalization_packet = {
        "claim": "Smoke proof claim.",
        "lean_code": "theorem smoke : True := by\n  sorry\n",
        "theorem_with_sorry": "theorem smoke : True := by\n  sorry\n",
        "theorem_name": "smoke",
        "imports": ["Mathlib"],
        "selected_imports": ["Mathlib"],
        "open_statements": [],
        "subgoals": [],
        "selected_preamble": ["bellman_operator"],
        "vacuity": {"is_vacuous": False},
        "faithfulness": {
            "score": 5.0,
            "coverage": 1.0,
            "structural_isomorphism": 1.0,
            "primitive_faithfulness": 1.0,
            "claim_frame": {},
            "stub_frame": {},
            "needs_human_review": False,
            "passes_gate": True,
            "feedback": [],
        },
        "parse_check": {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        "review_state": "approved",
        "backend": "leanstral",
        "provider": "mistral",
        "model": "labs-leanstral-2603",
    }

    class FakeProver:
        def __init__(self) -> None:
            self.primary_backend = SimpleNamespace(
                name="goedel-prover-v2",
                provider="huggingface",
                model="Goedel-LM/Goedel-Prover-V2-32B",
            )
            self.calls: list[dict[str, object]] = []

        async def prove(
            self,
            packet,
            job_id,
            *,
            max_turns,
            timeout,
            target_timeouts,
            allow_decomposition,
            benchmark_mode,
            on_progress=None,
        ):
            self.calls.append(
                {
                    "job_id": job_id,
                    "max_turns": max_turns,
                    "timeout": timeout,
                    "target_timeouts": target_timeouts.model_dump(mode="json") if target_timeouts is not None else None,
                    "allow_decomposition": allow_decomposition,
                    "benchmark_mode": benchmark_mode,
                }
            )
            if on_progress is not None:
                on_progress(
                    "prover_turn",
                    {
                        "event": "prover_turn",
                        "job_id": job_id,
                        "stage": "prover",
                        "status": "running_prover",
                        "message": "fake prover turn",
                        "metadata": {"tool_name": "apply_tactic"},
                    },
                )
            return ProverResult(
                status="verified",
                theorem_name=packet.theorem_name,
                claim=packet.claim,
                benchmark_mode=benchmark_mode,
                verified_code=packet.lean_code.replace("sorry", "trivial"),
                current_code=packet.lean_code.replace("sorry", "trivial"),
                trace=[],
                targets=[],
                failure=None,
                termination_reason="verified",
                repair_count=0,
                preamble_names=list(packet.selected_preamble),
                backend_used="goedel-prover-v2",
                attempted_backends=["goedel-prover-v2"],
                tool_budget={},
                telemetry={},
                usage_by_stage={
                    "prover": {
                        "stage": "prover",
                        "provider": "huggingface",
                        "model": "Goedel-LM/Goedel-Prover-V2-32B",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "estimated_cost_usd": 0.25,
                        "latency_ms": 10.0,
                        "success": True,
                        "usage_source": "provider",
                        "error_code": None,
                    }
                },
                timing_breakdown={"prover_ms": 10.0, "total_ms": 10.0},
                target_timeouts=target_timeouts,
                audit_summary={"event_count": 1, "events": []},
            )

    fake_prover = FakeProver()
    monkeypatch.setattr(api_module, "prover", fake_prover)
    client = TestClient(api_module.app)

    prove = client.post(
        "/prove",
        json={
            "formalization_packet": formalization_packet,
            "benchmark_mode": True,
            "target_timeouts": {"theorem_body": 300, "subgoal": 180, "apollo_lemma": 120},
        },
    )
    assert prove.status_code == 200
    job_id = prove.json()["job_id"]

    deadline = time.monotonic() + 2.0
    payload = None
    while time.monotonic() < deadline:
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        payload = poll.json()
        if payload["status"] not in {"queued", "running_prover"}:
            break
        time.sleep(0.05)

    assert payload is not None
    assert payload["status"] == "completed"
    assert payload["result"]["benchmark_mode"] is True
    assert payload["result"]["target_timeouts"] == {"theorem_body": 300, "subgoal": 180, "apollo_lemma": 120}
    assert fake_prover.calls
    assert fake_prover.calls[0]["target_timeouts"] == {"theorem_body": 300, "subgoal": 180, "apollo_lemma": 120}
    assert fake_prover.calls[0]["benchmark_mode"] is True


def test_prove_accepts_hydrated_formalize_result(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    client = TestClient(api_module.app)

    class FakeProver:
        def __init__(self) -> None:
            self.primary_backend = SimpleNamespace(
                name="leanstral",
                provider="mistral",
                model="labs-leanstral-2603",
            )

        async def prove(self, packet, job_id, **kwargs):
            return ProverResult(
                status="verified",
                theorem_name=packet.theorem_name,
                claim=packet.claim,
                benchmark_mode=kwargs["benchmark_mode"],
                verified_code=packet.lean_code.replace("sorry", "trivial"),
                current_code=packet.lean_code.replace("sorry", "trivial"),
                trace=[],
                targets=[],
                failure=None,
                termination_reason="verified",
                repair_count=0,
                preamble_names=list(packet.selected_preamble),
                backend_used="leanstral",
                attempted_backends=["leanstral"],
                tool_budget={},
                telemetry={},
                usage_by_stage={},
                timing_breakdown={"prover_ms": 0.0, "total_ms": 0.0},
                target_timeouts=kwargs["target_timeouts"],
                audit_summary={"event_count": 0, "events": []},
            )

    monkeypatch.setattr(api_module, "prover", FakeProver())

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": True})
    assert plan.status_code == 200
    formalize = client.post(
        "/formalize",
        json={
            "claim": "A Bellman equation claim.",
            "planner_packet": plan.json()["result"],
            "benchmark_mode": True,
        },
    )
    assert formalize.status_code == 200

    prove = client.post(
        "/prove",
        json={
            "formalization_packet": formalize.json()["result"],
            "benchmark_mode": True,
        },
    )

    assert prove.status_code == 200


def test_review_endpoint_transitions_are_honest(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    client = TestClient(api_module.app)

    plan = client.post("/plan", json={"claim": "A Bellman equation claim.", "benchmark_mode": False})
    assert plan.status_code == 200
    job_id = plan.json()["id"]

    review = client.post(
        f"/jobs/{job_id}/review",
        json={"stage": "plan", "decision": "approve", "notes": "Looks faithful."},
    )
    assert review.status_code == 200
    payload = review.json()
    assert payload["status"] == "completed"
    assert payload["review_state"] == "approved"
    assert payload["result"]["review"]["decision"] == "approve"
    assert payload["result"]["review_gate_honest"] is True


def test_health_metrics_and_prometheus(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api_module.planner,
        "connectivity_check",
        lambda: (False, "Local Ollama planner endpoint unreachable at http://127.0.0.1:11434"),
    )
    client = TestClient(api_module.app)

    health = client.get("/health")
    metrics = client.get("/metrics")
    prometheus = client.get("/metrics/prometheus")

    assert health.status_code == 200
    assert health.json()["runtime"]["backends"]["planner"]["provider_pinned"] in {True, False}
    assert health.json()["runtime"]["backends"]["planner"]["available"] is False
    assert health.json()["runtime"]["backends"]["planner"]["endpoint_reachable"] is False
    assert "Local Ollama planner endpoint unreachable" in health.json()["runtime"]["backends"]["planner"]["availability_reason"]
    assert "recent_success_rate_last_100" in health.json()["runtime"]

    assert metrics.status_code == 200
    metrics_payload = metrics.json()
    assert "backend_status" in metrics_payload
    assert metrics_payload["backend_status"]["planner"]["available"] is False
    assert "usage_totals" in metrics_payload
    assert "recent" in metrics_payload

    assert prometheus.status_code == 200
    assert "leanecon_benchmark_claims" in prometheus.text


def test_metrics_history_endpoint(monkeypatch, tmp_path) -> None:
    api_module = _configure_api(monkeypatch, tmp_path)
    history_path = tmp_path / "benchmark_history.jsonl"
    history_path.write_text(
        '{"row_id":"run_000001","timestamp":"2026-04-23T23:15:00+00:00","overall_pass_rate":0.865}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(api_module, "CANONICAL_HISTORY_PATH", history_path)
    client = TestClient(api_module.app)

    response = client.get("/metrics/history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(history_path)
    assert payload["rows"][0]["row_id"] == "run_000001"
