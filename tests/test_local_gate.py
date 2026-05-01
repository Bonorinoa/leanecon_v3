from __future__ import annotations

import json
from types import SimpleNamespace

from evals.benchmark_manifest import MANIFEST_PATH, build_manifest
from evals.local_gate import _combine_summaries, run_claim_set
from src.observability.models import ProviderCallMetadata
from src.formalizer.models import FaithfulnessAssessment, FormalizationPacket, ParseCheck
from src.planner import PlannerLLMResponse, PlannerService
from src.planner.planner import PlannerDriverError
from src.planner.retrieval import HashingTextEmbedder, PlannerRetrievalService
from src.prover.models import ProverResult, ProverTraceStep


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
        self.backend = SimpleNamespace(
            name="leanstral", provider="mistral", model="labs-leanstral-2603"
        )

    def formalize_with_telemetry(
        self,
        claim: str,
        *,
        planner_packet,
        theorem_stub,
        preamble_names,
        benchmark_mode,
    ):
        lean_code = (
            theorem_stub or "import Mathlib\n\ntheorem local_gate_stub : True := by\n  sorry\n"
        )
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
        self.primary_backend = SimpleNamespace(
            name="goedel-prover-v2", provider="huggingface", model="Goedel-LM/Goedel-Prover-V2-32B"
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
                "target_timeouts": target_timeouts.model_dump(mode="json")
                if target_timeouts is not None
                else None,
                "allow_decomposition": allow_decomposition,
                "benchmark_mode": benchmark_mode,
                "claim_type": packet.claim_type,
            }
        )
        if on_progress is not None:
            on_progress(
                "prover_turn",
                {
                    "event": "prover_turn",
                    "claim_id": packet.theorem_name,
                    "stage": "prover",
                    "status": "running",
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


class TraceFakeProver(FakeProver):
    async def prove(self, *args, **kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress(
                "retrieval_event",
                {
                    "event": "retrieval_event",
                    "stage": "prover",
                    "status": "running_prover",
                    "message": "retrieved premises",
                    "metadata": {
                        "RetrievalEvent": {
                            "event_type": "RetrievalEvent",
                            "retrieved_count": 1,
                            "hit": True,
                            "k": 5,
                        }
                    },
                },
            )
            on_progress(
                "progress_delta",
                {
                    "event": "progress_delta",
                    "stage": "prover",
                    "status": "running_prover",
                    "message": "recorded progress",
                    "metadata": {
                        "ProgressDelta": {
                            "event_type": "ProgressDelta",
                            "goals_reduced": True,
                            "complexity_reduced": True,
                            "stall_detected": False,
                        }
                    },
                },
            )
            on_progress(
                "synthesis_event",
                {
                    "event": "synthesis_event",
                    "stage": "prover",
                    "status": "running_prover",
                    "message": "recorded synthesis",
                    "metadata": {
                        "SynthesisEvent": {
                            "event_type": "SynthesisEvent",
                            "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                            "referenced_premises": ["tendsto_atTop_ciSup"],
                            "top3_match": True,
                            "success": True,
                            "target_name": "theorem_body",
                            "claim_id": "fake",
                            "decomposition_depth": 1,
                        }
                    },
                },
            )
        result = await super().prove(*args, **kwargs)
        result.trace = [
            ProverTraceStep(
                turn=1,
                backend="goedel-prover-v2",
                target_name="theorem_body",
                action_type="mathlib_native_harness_loop",
                success=True,
                tool_name="apply_tactic",
                tool_arguments={
                    "RetrievalEvent": {
                        "event_type": "RetrievalEvent",
                        "retrieved_count": 1,
                        "hit": True,
                        "k": 5,
                    },
                    "ToolUsageTrace": {
                        "event_type": "ToolUsageTrace",
                        "tool_name": "apply_tactic",
                        "state_hash_before": "before",
                        "state_hash_after": "after",
                    },
                    "ProgressDelta": {
                        "event_type": "ProgressDelta",
                        "goals_reduced": True,
                        "complexity_reduced": True,
                        "stall_detected": False,
                    },
                    "SynthesisEvent": {
                        "event_type": "SynthesisEvent",
                        "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                        "referenced_premises": ["tendsto_atTop_ciSup"],
                        "top3_match": True,
                        "success": True,
                        "target_name": "theorem_body",
                        "claim_id": "fake",
                        "decomposition_depth": 1,
                    },
                    "PremiseResolutionEvent": {
                        "event_type": "PremiseResolutionEvent",
                        "raw_name": "tendsto_atTop_ciSup",
                        "resolved_name": "tendsto_atTop_ciSup",
                        "resolved": True,
                        "source": "mathlib_rag",
                        "resolution_method": "raw_global_candidate",
                        "failure_reason": None,
                    },
                    "CandidateTacticEvent": {
                        "event_type": "CandidateTacticEvent",
                        "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                        "origin": "resolved_premise_micro_search",
                        "premise_name": "tendsto_atTop_ciSup",
                        "success": True,
                        "committed": True,
                        "progress_delta": {
                            "event_type": "ProgressDelta",
                            "goals_reduced": True,
                            "complexity_reduced": True,
                            "stall_detected": False,
                        },
                        "error": None,
                    },
                    "synthesis_candidate_used": True,
                },
                tool_result="All goals solved.",
            )
        ]
        result.synthesis_events = [
            {
                "event_type": "SynthesisEvent",
                "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                "referenced_premises": ["tendsto_atTop_ciSup"],
                "top3_match": True,
                "success": True,
                "target_name": "theorem_body",
                "claim_id": "fake",
                "decomposition_depth": 1,
            }
        ]
        result.tool_budget = {
            "total_tool_calls": 2,
            "lsp_tool_calls": 1,
            "native_search_attempts": 0,
        }
        return result


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
    assert (
        summary["cost_by_model"]["huggingface:Goedel-LM/Goedel-Prover-V2-32B"]["estimated_cost_usd"]
        == 0.36
    )
    assert all(item["theorem_stub_reference"] is not None for item in summary["results"])
    assert all(item["benchmark_mode"] is True for item in summary["results"])
    assert all("raw_planner_response" not in item for item in summary["results"])
    assert all(item["verified_via"] == "full_pipeline" for item in summary["results"])
    assert fake_prover.calls
    assert fake_prover.calls[0]["benchmark_mode"] is True
    assert fake_prover.calls[0]["timeout"] == 120
    assert fake_prover.calls[0]["target_timeouts"] == {
        "theorem_body": 120,
        "subgoal": 120,
        "apollo_lemma": 120,
    }
    assert fake_prover.calls[0]["claim_type"] is None
    assert summary["claim_set_manifest"]["claim_set"] == "tier0_smoke"
    assert summary["results"][0]["progress_events"]


def test_local_gate_attaches_claim_type_for_supported_benchmark_buckets(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    fake_prover = FakeProver()
    summary = run_claim_set(
        "tier2_frontier_mathlib_native",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=fake_prover,
        enforce_readiness=False,
        benchmark_mode=True,
    )

    assert summary["claims_total"] == len(fake_prover.calls)
    assert fake_prover.calls
    assert all(call["claim_type"] == "mathlib_native" for call in fake_prover.calls)


def test_local_gate_persists_raw_planner_response_for_schema_invalid(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    class RepairingPlannerDriver:
        raw_text = '{"plan_paragraph":"Map the claim to the measure axiom $\\\\mu(\\\\emptyset)=0$.","subgoals":["exact benchmark_measure_empty"]}'

        def generate(self, **_: object) -> PlannerLLMResponse:
            error = PlannerDriverError(
                "Planner backend returned schema-invalid JSON: missing required keys"
            )
            setattr(
                error,
                "provider_metadata",
                ProviderCallMetadata(
                    response_text=self.raw_text, raw_planner_response=self.raw_text
                ),
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
    assert all(
        item["usage_by_stage"]["planner"]["error_code"] == "schema_invalid"
        for item in summary["results"]
    )
    assert all(
        item["raw_planner_response"] == RepairingPlannerDriver.raw_text
        for item in summary["results"]
    )


def test_checked_in_benchmark_manifest_matches_claim_sets() -> None:
    checked_in = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert checked_in == build_manifest()


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

    shortcut_results = [
        item for item in summary["results"] if item.get("termination_reason") == "trivial_shortcut"
    ]
    assert len(shortcut_results) == 1
    assert shortcut_results[0]["trivial_shortcut"] == {
        "hypothesis": "hspend",
        "tactic": "exact hspend",
    }
    assert shortcut_results[0]["status"] == "verified"
    assert shortcut_results[0]["verified_via"] == "trivial_shortcut"
    assert shortcut_results[0]["usage_by_stage"] == {}
    assert len(fake_prover.calls) == summary["claims_total"] - 1


def test_local_gate_blocks_unreachable_planner_endpoint(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    planner_service = _planner_service()
    monkeypatch.setattr(
        planner_service,
        "connectivity_check",
        lambda: (
            False,
            "Local Ollama planner endpoint unreachable at http://127.0.0.1:11434 (connect: operation not permitted)",
        ),
    )

    summary = run_claim_set(
        "tier0_smoke",
        planner_service=planner_service,
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=True,
        benchmark_mode=True,
    )

    assert summary["executed"] is False
    assert summary["failure_counts"] == {"planner_endpoint_reachable": 1}
    assert summary["readiness"]["checks"]["planner_endpoint_reachable"] is False
    assert (
        "Local Ollama planner endpoint unreachable"
        in summary["readiness"]["details"]["planner_endpoint_reachable"]
    )


def test_local_gate_seeded_sampling_is_reproducible(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    summary_a = run_claim_set(
        "tier1_core_preamble_definable",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        limit=3,
        sample_seed=17,
    )
    summary_b = run_claim_set(
        "tier1_core_preamble_definable",
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


def test_local_gate_focused_sample_uses_locked_frontier_ids(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    mathlib_summary = run_claim_set(
        "tier2_frontier_mathlib_native",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        focused_sample=True,
    )
    preamble_summary = run_claim_set(
        "tier2_frontier_preamble_definable",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=FakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        focused_sample=True,
    )

    assert mathlib_summary["sampling_mode"] == "focused_sample"
    assert mathlib_summary["claims_total"] == 3
    assert mathlib_summary["selected_ids"] == [
        "t2_contraction_mapping_fixed_point",
        "t2_extreme_value_repair",
        "t2_monotone_sequence_converges",
    ]
    assert preamble_summary["sampling_mode"] == "focused_sample"
    assert preamble_summary["claims_total"] == 9
    assert preamble_summary["selected_ids"][-1] == "t2_indirect_utility_roys_identity"
    assert "t2_geometric_series_discount" not in preamble_summary["selected_ids"]


def test_local_gate_benchmark_metrics_include_harness_trace_events(monkeypatch) -> None:
    import evals.local_gate as local_gate_module

    monkeypatch.setattr(local_gate_module, "_try_claim_trivial_shortcut", lambda _stub: None)

    summary = run_claim_set(
        "tier2_frontier_mathlib_native",
        planner_service=_planner_service(),
        formalizer_service=FakeFormalizerService(),
        prover_instance=TraceFakeProver(),
        enforce_readiness=False,
        benchmark_mode=True,
        focused_sample=True,
    )
    combined = _combine_summaries([summary])

    assert summary["retrieval_hit_rate@5"] == 1.0
    assert summary["avg_tool_calls_mathlib"] == 2.0
    assert summary["synthesis_efficiency"] == 1.0
    assert summary["premise_match_rate@3"] == 1.0
    assert summary["synthesis_event_count"] == 1
    assert summary["premise_matched_synthesis_event_count"] == 1
    assert summary["premise_top3_synthesis_event_count"] == 1
    assert summary["synthesis_candidate_used_count"] == 3
    assert summary["resolved_premise_rate"] == 1.0
    assert summary["candidate_attempt_count"] == 1
    assert summary["candidate_success_rate"] == 1.0
    assert summary["provider_fallback_rate"] == 1.0
    assert summary["repl_compile_disagreement_count"] == 0
    assert summary["avg_decomposition_depth_mathlib"] == 0.0
    assert summary["progress_deltas"]
    assert summary["synthesis_events"]
    assert combined["retrieval_hit_rate@5"] == 1.0
    assert combined["avg_tool_calls_mathlib"] == 2.0
    assert combined["synthesis_efficiency"] == 1.0
    assert combined["premise_match_rate@3"] == 1.0
    assert combined["synthesis_event_count"] == 1
    assert combined["premise_matched_synthesis_event_count"] == 1
    assert combined["premise_top3_synthesis_event_count"] == 1
    assert combined["synthesis_candidate_used_count"] == 3
    assert combined["resolved_premise_rate"] == 1.0
    assert combined["candidate_attempt_count"] == 1
    assert combined["candidate_success_rate"] == 1.0
    assert combined["provider_fallback_rate"] == 1.0
    assert combined["repl_compile_disagreement_count"] == 0
    first_result = summary["results"][0]
    assert {event["event_type"] for event in first_result["trace_events"]} >= {
        "RetrievalEvent",
        "ToolUsageTrace",
        "ProgressDelta",
        "SynthesisEvent",
        "PremiseResolutionEvent",
        "CandidateTacticEvent",
    }
    assert any(
        (event.get("metadata") or {}).get("RetrievalEvent")
        for event in first_result["progress_events"]
    )


def test_local_gate_main_emits_readable_terminal_summary(monkeypatch, tmp_path, capsys) -> None:
    import evals.local_gate as local_gate_module

    def fake_summary(
        claim_set: str, *, passed: int, total: int, failures: dict[str, int]
    ) -> dict[str, object]:
        bucket = "mathlib_native" if "mathlib_native" in claim_set else "preamble_definable"
        failed = total - passed
        return {
            "claim_set": claim_set,
            "benchmark_mode": True,
            "mode": "benchmark_pipeline",
            "generated_at": "2026-04-23T23:00:00+00:00",
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
            "claim_set_manifest": {
                "bucket_counts": {
                    "mathlib_native": total if bucket == "mathlib_native" else 0,
                    "planner_formalizer": 0,
                    "preamble_definable": total if bucket == "preamble_definable" else 0,
                    "prover_search": 0,
                    "regression": 0,
                }
            },
            "results": [
                {
                    "id": f"{claim_set}_{index}",
                    "benchmark_bucket": bucket,
                    "status": "verified" if index < passed else "failed",
                    "failure_code": None if index < passed else "max_turns_exhausted",
                    "termination_reason": "verified" if index < passed else "no_progress_stall",
                    "verified_via": "full_pipeline",
                    "tool_calls": 1 if index < passed else 2,
                    "decomposition_depth": 0 if index < passed else 1,
                    "timing_breakdown": {
                        "planner_ms": 1000.0,
                        "formalizer_ms": 2000.0,
                        "prover_ms": 3000.0,
                        "total_ms": 6000.0,
                    },
                    "progress_events": (
                        [{"message": "Closed via direct definable closure.", "metadata": {}}]
                        if index == 0 and index < passed
                        else []
                    ),
                }
                for index in range(total)
            ],
        }

    summaries = iter(
        [
            fake_summary("tier0_smoke", passed=3, total=3, failures={}),
            fake_summary(
                "tier1_core_preamble_definable",
                passed=1,
                total=2,
                failures={"max_turns_exhausted": 1},
            ),
        ]
    )

    monkeypatch.setattr(local_gate_module, "run_claim_set", lambda *args, **kwargs: next(summaries))
    monkeypatch.setattr(
        local_gate_module,
        "write_summary",
        lambda name, payload, output_dir=None: (output_dir or tmp_path) / f"{name}.json",
    )

    exit_code = local_gate_module.main(
        [
            "--benchmark-mode",
            "--claim-sets",
            "tier0_smoke,tier1_core_preamble_definable",
            "--save-history",
            "--output-dir",
            str(tmp_path),
        ]
    )
    output = capsys.readouterr().out
    history_path = tmp_path / "benchmark_history.jsonl"
    history_rows = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert exit_code == 1
    assert "[tier0_smoke] summary" in output
    assert "[heartbeat" not in output
    assert "| Metric" in output
    assert "| Stage" in output
    assert "| Failure code" in output
    assert "[local_gate] combined" in output
    assert "History updated: run_000001" in output
    assert len(history_rows) == 1
    assert history_rows[0]["row_id"] == "run_000001"
    assert "tier1_core_preamble_definable" in history_rows[0]["bucket_breakdown"]
