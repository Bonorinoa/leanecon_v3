from __future__ import annotations

from types import SimpleNamespace

from evals.local_gate import run_claim_set
from src.formalizer import FormalizerGenerationResponse, FormalizerService, FormalizerSubgoal
from src.planner import PlannerLLMResponse, PlannerService
from src.prover.models import ProverResult


class FakePlannerDriver:
    def generate(self, **_: object) -> PlannerLLMResponse:
        return PlannerLLMResponse.model_validate(
            {
                "clarifying_questions": [],
                "textbook_defaults": ["Use the canonical benchmark assumptions."],
                "plan_paragraph": "Map the claim to the right LeanEcon preamble and emit proof-oriented subgoals around $x = x$.",
                "subgoals": [
                    "theorem local_gate_subgoal_1 : True := by\n  sorry",
                    "theorem local_gate_subgoal_2 : True := by\n  sorry",
                    "theorem local_gate_subgoal_3 : True := by\n  sorry",
                ],
                "needs_review": False,
                "confidence": 0.9,
            }
        )


class FakeFormalizerDriver:
    def generate(self, **_: object) -> FormalizerGenerationResponse:
        return FormalizerGenerationResponse(
            theorem_name="local_gate_stub",
            theorem_docstring="Deterministic theorem stub for benchmark tests.",
            theorem_statement="True",
            open_statements=[],
            subgoals=[
                FormalizerSubgoal(
                    name="h_local_gate",
                    statement="True",
                    rationale="Synthetic subgoal for deterministic tests.",
                )
            ],
            final_expression=None,
        )


class FakeProver:
    def __init__(self) -> None:
        self.primary_backend = SimpleNamespace(name="goedel-prover-v2", provider="huggingface", model="Goedel-LM/Goedel-Prover-V2-32B")
        self.calls: list[dict[str, object]] = []

    async def prove(self, packet, job_id, *, max_turns, timeout, target_timeouts, allow_decomposition, benchmark_mode):
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
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "estimated_cost_usd": 0.12,
                    "latency_ms": 25.0,
                    "success": True,
                    "usage_source": "provider",
                    "error_code": None,
                }
            },
            timing_breakdown={"prover_ms": 25.0, "total_ms": 25.0},
            target_timeouts=target_timeouts,
            audit_summary={"event_count": 1, "events": []},
        )


def test_local_gate_runs_live_pipeline_with_usage_summary() -> None:
    fake_prover = FakeProver()
    summary = run_claim_set(
        "tier0_smoke",
        planner_service=PlannerService(driver=FakePlannerDriver()),
        formalizer_service=FormalizerService(mistral_driver=FakeFormalizerDriver()),
        prover_instance=fake_prover,
        enforce_readiness=False,
    )

    assert summary["executed"] is True
    assert summary["benchmark_mode"] is True
    assert summary["claims_total"] == 3
    assert summary["claims_passed"] == 3
    assert summary["claims_failed"] == 0
    assert summary["target_timeouts"] == {"theorem_body": 300, "subgoal": 180, "apollo_lemma": 120}
    assert summary["tokens_by_stage"]["prover"]["input_tokens"] == 360
    assert summary["cost_by_stage"]["prover"] == 0.36
    assert summary["cost_by_model"]["huggingface:Goedel-LM/Goedel-Prover-V2-32B"]["estimated_cost_usd"] == 0.36
    assert all(item["theorem_stub_reference"] is not None for item in summary["results"])
    assert all(item["benchmark_mode"] is True for item in summary["results"])
    assert fake_prover.calls
    assert fake_prover.calls[0]["benchmark_mode"] is True
    assert fake_prover.calls[0]["target_timeouts"] == {"theorem_body": 300, "subgoal": 180, "apollo_lemma": 120}
