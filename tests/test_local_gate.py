from __future__ import annotations

from types import SimpleNamespace

from evals.local_gate import run_claim_set
from src.observability.models import ProviderCallMetadata
from src.formalizer.models import FaithfulnessAssessment, FormalizationPacket, ParseCheck
from src.planner import PlannerLLMResponse, PlannerService
from src.planner.planner import PlannerDriverError
from src.planner.retrieval import HashingTextEmbedder, PlannerRetrievalService
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


class FakeFormalizerService:
    def __init__(self) -> None:
        self.backend = SimpleNamespace(name="leanstral", provider="mistral", model="labs-leanstral-2603")

    def formalize_with_telemetry(
        self,
        claim: str,
        *,
        planner_packet,
        theorem_stub,
        preamble_names,
        benchmark_mode,
    ):
        lean_code = theorem_stub or "import Mathlib\n\ntheorem local_gate_stub : True := by\n  sorry\n"
        theorem_name = "local_gate_stub"
        for line in lean_code.splitlines():
            stripped = line.strip()
            if stripped.startswith("theorem "):
                theorem_name = stripped.split()[1]
                break
        packet = FormalizationPacket.model_validate(
            {
                "claim": claim,
                "lean_code": lean_code,
                "theorem_with_sorry": lean_code,
                "theorem_name": theorem_name,
                "imports": ["Mathlib"],
                "selected_imports": ["Mathlib"],
                "open_statements": [],
                "subgoals": [],
                "selected_preamble": list(preamble_names or []),
                "vacuity": {"is_vacuous": False},
                "faithfulness": FaithfulnessAssessment(
                    score=5.0,
                    coverage=1.0,
                    structural_isomorphism=1.0,
                    primitive_faithfulness=1.0,
                    claim_frame={},
                    stub_frame={},
                    needs_human_review=False,
                    passes_gate=True,
                    feedback=[],
                ),
                "parse_check": ParseCheck(success=True, exit_code=0, stdout="", stderr=""),
                "review_state": "approved" if benchmark_mode else "awaiting_formalization_review",
                "backend": "leanstral",
                "provider": "mistral",
                "model": "labs-leanstral-2603",
            }
        )
        usage_payload = {
            "stage": "formalizer",
            "provider": "mistral",
            "model": "labs-leanstral-2603",
            "input_tokens": 60,
            "output_tokens": 20,
            "estimated_cost_usd": 0.0,
            "latency_ms": 10.0,
            "success": True,
            "usage_source": "provider",
            "error_code": None,
        }
        usage = SimpleNamespace(latency_ms=10.0, to_dict=lambda: dict(usage_payload))
        return SimpleNamespace(payload=packet, usage=usage)


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


def _planner_service() -> PlannerService:
    return PlannerService(
        driver=FakePlannerDriver(),
        retrieval_service=PlannerRetrievalService(embedder=HashingTextEmbedder()),
    )


def test_local_gate_runs_live_pipeline_with_usage_summary(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    fake_prover = FakeProver()
    summary = run_claim_set(
        "tier0_smoke",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=fake_prover,
        enforce_readiness=False,
        benchmark_mode=True,
    )

    assert summary["executed"] is True
    assert summary["benchmark_mode"] is True
    assert summary["claims_total"] == 3
    assert summary["claims_passed"] == 3
    assert summary["claims_failed"] == 0
    assert summary["target_timeouts"] == {"theorem_body": 120, "subgoal": 120, "apollo_lemma": 120}
    assert summary["tokens_by_stage"]["prover"]["input_tokens"] == 360
    assert summary["cost_by_stage"]["prover"] == 0.36
    assert summary["cost_by_model"]["huggingface:Goedel-LM/Goedel-Prover-V2-32B"]["estimated_cost_usd"] == 0.36
    assert all(item["theorem_stub_reference"] is not None for item in summary["results"])
    assert all(item["benchmark_mode"] is True for item in summary["results"])
    assert all("raw_planner_response" not in item for item in summary["results"])
    assert all(item["verified_via"] == "full_pipeline" for item in summary["results"])
    assert fake_prover.calls
    assert fake_prover.calls[0]["benchmark_mode"] is True
    assert fake_prover.calls[0]["timeout"] == 120
    assert fake_prover.calls[0]["target_timeouts"] == {"theorem_body": 120, "subgoal": 120, "apollo_lemma": 120}


def test_local_gate_persists_raw_planner_response_for_schema_invalid(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    class RepairingPlannerDriver:
        raw_text = (
            '{"plan_paragraph":"Map the claim to the measure axiom $\\\\mu(\\\\emptyset)=0$.","subgoals":["exact benchmark_measure_empty"]}'
        )

        def generate(self, **_: object) -> PlannerLLMResponse:
            error = PlannerDriverError("Planner backend returned schema-invalid JSON: missing required keys")
            setattr(
                error,
                "provider_metadata",
                ProviderCallMetadata(response_text=self.raw_text, raw_planner_response=self.raw_text),
            )
            raise error

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)
    summary = run_claim_set(
        "tier0_smoke",
        planner_service=PlannerService(
            driver=RepairingPlannerDriver(),
            retrieval_service=PlannerRetrievalService(embedder=HashingTextEmbedder()),
        ),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
    )

    assert all(item["usage_by_stage"]["planner"]["error_code"] == "schema_invalid" for item in summary["results"])
    assert all(item["raw_planner_response"] == RepairingPlannerDriver.raw_text for item in summary["results"])


def test_local_gate_uses_trivial_shortcut_and_skips_pipeline(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    def fake_shortcut(theorem_stub):
        if theorem_stub and "hspend" in theorem_stub:
            return {
                "theorem_name": "benchmark_budget_constraint",
                "hypothesis": "hspend",
                "tactic": "exact hspend",
                "verified_code": theorem_stub.replace("sorry", "exact hspend"),
            }
        return None

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", fake_shortcut)

    fake_prover = FakeProver()
    summary = run_claim_set(
        "tier0_smoke",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=fake_prover,
        enforce_readiness=False,
        benchmark_mode=False,
    )

    shortcut_results = [item for item in summary["results"] if item.get("termination_reason") == "trivial_shortcut"]
    assert len(shortcut_results) == 1
    assert shortcut_results[0]["trivial_shortcut"] == {
        "hypothesis": "hspend",
        "tactic": "exact hspend",
    }
    assert shortcut_results[0]["status"] == "verified"
    assert shortcut_results[0]["verified_via"] == "trivial_shortcut"
    assert shortcut_results[0]["usage_by_stage"] == {}
    assert len(fake_prover.calls) == summary["claims_total"] - 1


def test_local_gate_seeded_sampling_is_reproducible(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    summary_a = run_claim_set(
        "tier1_core",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        limit=3,
        sample_seed=17,
    )
    summary_b = run_claim_set(
        "tier1_core",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        limit=3,
        sample_seed=17,
    )

    assert summary_a["sampling_mode"] == "seeded_random"
    assert summary_a["sample_seed"] == 17
    assert len(summary_a["selected_ids"]) == 3
    assert summary_a["selected_ids"] == summary_b["selected_ids"]


def test_local_gate_main_emits_readable_terminal_summary(monkeypatch, tmp_path, capsys) -> None:
    import evals.local_gate as local_gate_module

    def fake_summary(claim_set: str, *, passed: int, total: int, failures: dict[str, int]) -> dict[str, object]:
        failed = total - passed
        return {
            "claim_set": claim_set,
            "benchmark_mode": True,
            "claims_total": total,
            "claims_passed": passed,
            "claims_failed": failed,
            "pass_at_1": passed / total,
            "executed": True,
            "readiness": {"ready": True, "blockers": [], "checks": {}},
            "tokens_by_stage": {},
            "cost_by_stage": {"planner": 0.01 if passed else 0.0},
            "cost_by_model": {},
            "failure_counts": failures,
            "results": [
                {
                    "id": f"{claim_set}_{index}",
                    "status": "verified" if index < passed else "failed",
                    "failure_code": None if index < passed else "max_turns_exhausted",
                    "verified_via": "full_pipeline",
                    "timing_breakdown": {
                        "planner_ms": 1000.0,
                        "formalizer_ms": 2000.0,
                        "prover_ms": 3000.0,
                        "total_ms": 6000.0,
                    },
                }
                for index in range(total)
            ],
        }

    summaries = iter(
        [
            fake_summary("tier0_smoke", passed=3, total=3, failures={}),
            fake_summary("tier1_core", passed=1, total=2, failures={"max_turns_exhausted": 1}),
        ]
    )

    monkeypatch.setattr(local_gate_module, "run_claim_set", lambda *args, **kwargs: next(summaries))
    monkeypatch.setattr(
        local_gate_module,
        "write_summary",
        lambda name, payload, output_dir=None: (output_dir or tmp_path) / f"{name}.json",
    )

    exit_code = local_gate_module.main(
        ["--benchmark-mode", "--claim-set", "tier0_smoke", "--claim-set", "tier1_core", "--output-dir", str(tmp_path)]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[tier0_smoke] summary" in output
    assert "| Metric" in output
    assert "| Stage" in output
    assert "| Failure code" in output
    assert "[local_gate] combined" in output
