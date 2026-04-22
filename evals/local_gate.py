"""Live local-gate benchmark runner."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from evals.common import load_claims, write_summary
from src.config import BENCHMARK_REQUIRE_PRICING, PROVER_PROVIDER
from src.formalizer import DEFAULT_FORMALIZER, FormalizerService
from src.lean import compile_check
from src.observability import StageExecutionError, classify_exception, lookup_pricing
from src.planner import PlannerService
from src.providers import normalize_huggingface_provider
from src.prover import DEFAULT_PROVER, Prover, ProverTargetTimeouts
from src.prover.prover import _replace_named_theorem_body
from src.prover.tactics import direct_hypothesis_name

CLAIM_SETS = ("tier0_smoke", "tier1_core", "tier2_frontier")
LIVE_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=300, subgoal=180, apollo_lemma=120)
BENCHMARK_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=120, subgoal=120, apollo_lemma=120)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _sanitize_job_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "benchmark_claim"


def _usage_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return None


def _planner_raw_response(plan_result: Any) -> tuple[bool, str | None]:
    usage = _usage_dict(getattr(plan_result, "usage", None)) or {}
    if usage.get("error_code") != "schema_invalid":
        return False, None
    for event in getattr(plan_result, "audit_events", []):
        raw_response = getattr(event, "raw_planner_response", None)
        if raw_response is not None:
            return True, raw_response
        metadata = getattr(event, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("raw_planner_response") is not None:
            return True, str(metadata["raw_planner_response"])
    return True, None


def _audit_raw_response(events: list[Any]) -> str | None:
    for event in events:
        if getattr(event, "error_code", None) != "schema_invalid":
            continue
        raw_response = getattr(event, "raw_planner_response", None)
        if raw_response is not None:
            return raw_response
        metadata = getattr(event, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("raw_planner_response") is not None:
            return str(metadata["raw_planner_response"])
    return None


def _accumulate_usage(
    usage: dict[str, Any] | None,
    *,
    tokens_by_stage: dict[str, dict[str, int]],
    cost_by_stage: dict[str, float],
    cost_by_model: dict[str, dict[str, Any]],
) -> None:
    if not usage:
        return
    stage = str(usage.get("stage") or "unknown")
    provider = str(usage.get("provider") or "unknown")
    model = str(usage.get("model") or "unknown")
    stage_bucket = tokens_by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0})
    stage_bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    stage_bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    cost_by_stage[stage] = round(cost_by_stage.get(stage, 0.0) + float(usage.get("estimated_cost_usd") or 0.0), 8)
    model_key = f"{provider}:{model}"
    model_bucket = cost_by_model.setdefault(
        model_key,
        {"provider": provider, "model": model, "estimated_cost_usd": 0.0},
    )
    model_bucket["estimated_cost_usd"] = round(
        float(model_bucket["estimated_cost_usd"]) + float(usage.get("estimated_cost_usd") or 0.0),
        8,
    )


_THEOREM_NAME_RE = re.compile(r"(?m)^\s*(?:theorem|lemma)\s+([A-Za-z0-9_']+)")


def _extract_theorem_name(theorem_stub: str) -> str | None:
    match = _THEOREM_NAME_RE.search(theorem_stub)
    return match.group(1) if match else None


def _try_claim_trivial_shortcut(theorem_stub: str | None) -> dict[str, Any] | None:
    if not theorem_stub:
        return None
    theorem_name = _extract_theorem_name(theorem_stub)
    hypothesis = direct_hypothesis_name(theorem_stub)
    if not theorem_name or not hypothesis:
        return None
    tactic = f"exact {hypothesis}"
    try:
        candidate_code = _replace_named_theorem_body(theorem_stub, theorem_name, tactic)
    except ValueError:
        return None
    try:
        result = compile_check(candidate_code, timeout=60)
    except Exception:
        return None
    if not result.get("success"):
        return None
    return {
        "theorem_name": theorem_name,
        "hypothesis": hypothesis,
        "tactic": tactic,
        "verified_code": candidate_code,
    }


def _accumulate_failure(error_code: str | None, failure_counts: dict[str, int]) -> None:
    if not error_code:
        return
    failure_counts[error_code] = failure_counts.get(error_code, 0) + 1


def _preflight(
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
) -> dict[str, Any]:
    planner_backend = planner_service.backend
    formalizer_backend = formalizer_service.backend
    prover_backend = prover_instance.primary_backend
    planner_provider = planner_backend.provider
    planner_platform = "ollama" if planner_backend.name == "ollama-cloud" else "huggingface"
    prover_provider = PROVER_PROVIDER if prover_backend.provider == "huggingface" else prover_backend.provider
    checks = {
        "planner_provider_configured": (
            bool(planner_provider.strip())
            if planner_platform == "ollama"
            else normalize_huggingface_provider(planner_provider) in {"auto", planner_provider.strip()}
        ),
        "prover_provider_configured": prover_backend.provider != "huggingface"
        or normalize_huggingface_provider(prover_provider) in {"auto", prover_provider.strip()},
        "planner_price_known": True,
        "formalizer_price_known": True,
        "prover_price_known": True,
    }
    if BENCHMARK_REQUIRE_PRICING:
        checks["planner_price_known"] = lookup_pricing(planner_platform, planner_backend.model) is not None
        checks["formalizer_price_known"] = lookup_pricing(formalizer_backend.provider, formalizer_backend.model) is not None
        checks["prover_price_known"] = lookup_pricing(
            "huggingface" if prover_backend.provider == "huggingface" else prover_provider,
            prover_backend.model,
        ) is not None
    ready = all(checks.values())
    blockers = [name for name, status in checks.items() if not status]
    return {"ready": ready, "checks": checks, "blockers": blockers}


def _select_claims(
    claims: list[dict[str, Any]],
    *,
    limit: int | None,
    stratified: bool,
) -> list[dict[str, Any]]:
    if limit is None or limit >= len(claims):
        return claims
    if not stratified:
        return claims[:limit]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        preambles = claim.get("preamble_names") or []
        key = str(preambles[0]) if preambles else ""
        buckets.setdefault(key, []).append(claim)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progressed = False
        for key in sorted(buckets):
            bucket = buckets[key]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


async def _run_claim_set_async(
    claim_set: str,
    *,
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
    enforce_readiness: bool,
    benchmark_mode: bool,
    limit: int | None,
    stratified: bool,
) -> dict[str, Any]:
    claims = _select_claims(load_claims(claim_set), limit=limit, stratified=stratified)
    readiness = _preflight(planner_service, formalizer_service, prover_instance)
    target_timeouts = BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS
    if enforce_readiness and not readiness["ready"]:
        return {
            "claim_set": claim_set,
            "mode": "live_pipeline",
            "benchmark_mode": benchmark_mode,
            "target_timeouts": target_timeouts.model_dump(mode="json"),
            "generated_at": _timestamp(),
            "claims_total": len(claims),
            "claims_passed": 0,
            "claims_failed": len(claims),
            "pass_at_1": 0.0,
            "executed": False,
            "readiness": readiness,
            "tokens_by_stage": {},
            "cost_by_stage": {},
            "cost_by_model": {},
            "failure_counts": {blocker: 1 for blocker in readiness["blockers"]},
            "results": [],
        }

    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
    failure_counts: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for claim in claims:
        claim_id = str(claim["id"])
        raw_claim = str(claim["raw_claim"])
        theorem_stub = claim.get("theorem_stub")
        preamble_names_raw = claim.get("preamble_names") or []
        preamble_names = [str(name) for name in preamble_names_raw if str(name).strip()]
        planner_usage: dict[str, Any] | None = None
        formalizer_usage: dict[str, Any] | None = None
        prover_usage: dict[str, Any] | None = None
        planner_schema_invalid = False
        raw_planner_response: str | None = None
        failure_code: str | None = None
        termination_reason: str | None = None
        stage_timings = {"planner_ms": 0.0, "formalizer_ms": 0.0, "prover_ms": 0.0, "total_ms": 0.0}
        result_status = "failed"
        theorem_name: str | None = None
        verified_via = "full_pipeline"

        shortcut = None if benchmark_mode else _try_claim_trivial_shortcut(theorem_stub)
        if shortcut is not None:
            theorem_name = shortcut["theorem_name"]
            result_status = "verified"
            termination_reason = "trivial_shortcut"
            verified_via = "trivial_shortcut"
            failure_code = None
            _accumulate_failure(failure_code, failure_counts)
            results.append(
                {
                    "id": claim_id,
                    "status": result_status,
                    "termination_reason": termination_reason,
                    "failure_code": failure_code,
                    "theorem_name": theorem_name,
                    "raw_claim": raw_claim,
                    "benchmark_mode": benchmark_mode,
                    "verified_via": "trivial_shortcut",
                    "target_timeouts": target_timeouts.model_dump(mode="json"),
                    "theorem_stub_reference": theorem_stub,
                    "timing_breakdown": stage_timings,
                    "usage_by_stage": {},
                    "trivial_shortcut": {
                        "hypothesis": shortcut["hypothesis"],
                        "tactic": shortcut["tactic"],
                    },
                }
            )
            continue

        try:
            plan_result = planner_service.build_plan_with_telemetry(
                raw_claim,
                theorem_stub=theorem_stub,
                preamble_names=preamble_names,
                benchmark_mode=benchmark_mode,
            )
            planner_usage = _usage_dict(plan_result.usage)
            planner_schema_invalid, raw_planner_response = _planner_raw_response(plan_result)
            stage_timings["planner_ms"] = float(plan_result.usage.latency_ms or 0.0)

            formalize_result = formalizer_service.formalize_with_telemetry(
                raw_claim,
                planner_packet=plan_result.payload.model_dump(mode="json"),
                theorem_stub=theorem_stub,
                preamble_names=preamble_names,
                benchmark_mode=benchmark_mode,
            )
            formalizer_usage = _usage_dict(formalize_result.usage)
            stage_timings["formalizer_ms"] = float(formalize_result.usage.latency_ms or 0.0)

            prove_result = await prover_instance.prove(
                formalize_result.payload,
                f"local_gate_{_sanitize_job_id(claim_id)}",
                max_turns=8,
                timeout=120 if benchmark_mode else 300,
                target_timeouts=target_timeouts,
                allow_decomposition=True,
                benchmark_mode=benchmark_mode,
            )
            theorem_name = prove_result.theorem_name
            termination_reason = prove_result.termination_reason
            verified_via = prove_result.verified_via
            stage_timings["prover_ms"] = float(prove_result.timing_breakdown.get("prover_ms") or 0.0)
            stage_timings["total_ms"] = (
                stage_timings["planner_ms"] + stage_timings["formalizer_ms"] + stage_timings["prover_ms"]
            )
            prover_usage = _usage_dict(prove_result.usage_by_stage.get("prover"))
            if prove_result.failure is not None:
                failure_code = prove_result.failure.error_code or prove_result.failure.reason
            result_status = prove_result.status
        except StageExecutionError as exc:
            usage = _usage_dict(exc.usage)
            if exc.stage == "planner":
                planner_usage = usage
                stage_timings["planner_ms"] = float((usage or {}).get("latency_ms") or 0.0)
                planner_schema_invalid = exc.error_code == "schema_invalid"
                raw_planner_response = _audit_raw_response(exc.audit_events)
            elif exc.stage == "formalizer":
                formalizer_usage = usage
                stage_timings["formalizer_ms"] = float((usage or {}).get("latency_ms") or 0.0)
            failure_code = exc.error_code
            termination_reason = exc.stage
        except Exception as exc:
            failure_code = classify_exception(exc)
            termination_reason = "exception"

        stage_timings["total_ms"] = round(
            stage_timings["planner_ms"] + stage_timings["formalizer_ms"] + stage_timings["prover_ms"],
            3,
        )
        _accumulate_usage(planner_usage, tokens_by_stage=tokens_by_stage, cost_by_stage=cost_by_stage, cost_by_model=cost_by_model)
        _accumulate_usage(
            formalizer_usage,
            tokens_by_stage=tokens_by_stage,
            cost_by_stage=cost_by_stage,
            cost_by_model=cost_by_model,
        )
        _accumulate_usage(prover_usage, tokens_by_stage=tokens_by_stage, cost_by_stage=cost_by_stage, cost_by_model=cost_by_model)
        _accumulate_failure(failure_code, failure_counts)
        results.append(
            {
                "id": claim_id,
                "status": result_status,
                "termination_reason": termination_reason,
                "failure_code": failure_code,
                "theorem_name": theorem_name,
                "raw_claim": raw_claim,
                "benchmark_mode": benchmark_mode,
                "verified_via": verified_via,
                "target_timeouts": target_timeouts.model_dump(mode="json"),
                "theorem_stub_reference": theorem_stub,
                "timing_breakdown": stage_timings,
                "usage_by_stage": {
                    key: value
                    for key, value in {
                        "planner": planner_usage,
                        "formalizer": formalizer_usage,
                        "prover": prover_usage,
                    }.items()
                    if value is not None
                },
                **({"raw_planner_response": raw_planner_response} if planner_schema_invalid else {}),
            }
        )

    claims_passed = sum(1 for item in results if item["status"] == "verified")
    claims_total = len(results)
    return {
        "claim_set": claim_set,
        "mode": "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "executed": True,
        "readiness": readiness,
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "failure_counts": failure_counts,
        "results": results,
    }


def run_claim_set(
    claim_set: str,
    *,
    planner_service: PlannerService | None = None,
    formalizer_service: FormalizerService | None = None,
    prover_instance: Prover | None = None,
    enforce_readiness: bool = True,
    benchmark_mode: bool = False,
    limit: int | None = None,
    stratified: bool = False,
) -> dict[str, Any]:
    return asyncio.run(
        _run_claim_set_async(
            claim_set,
            planner_service=planner_service or PlannerService(),
            formalizer_service=formalizer_service or DEFAULT_FORMALIZER,
            prover_instance=prover_instance or DEFAULT_PROVER,
            enforce_readiness=enforce_readiness,
            benchmark_mode=benchmark_mode,
            limit=limit,
            stratified=stratified,
        )
    )


def _combine_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
    failure_counts: dict[str, int] = {}
    for summary in summaries:
        for stage, payload in summary.get("tokens_by_stage", {}).items():
            bucket = tokens_by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += int(payload.get("input_tokens") or 0)
            bucket["output_tokens"] += int(payload.get("output_tokens") or 0)
        for stage, cost in summary.get("cost_by_stage", {}).items():
            cost_by_stage[stage] = round(cost_by_stage.get(stage, 0.0) + float(cost), 8)
        for model_key, payload in summary.get("cost_by_model", {}).items():
            bucket = cost_by_model.setdefault(
                model_key,
                {
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "estimated_cost_usd": 0.0,
                },
            )
            bucket["estimated_cost_usd"] = round(
                float(bucket["estimated_cost_usd"]) + float(payload.get("estimated_cost_usd") or 0.0),
                8,
            )
        for error_code, count in summary.get("failure_counts", {}).items():
            failure_counts[error_code] = failure_counts.get(error_code, 0) + int(count)
    claims_total = sum(int(summary.get("claims_total") or 0) for summary in summaries)
    claims_passed = sum(int(summary.get("claims_passed") or 0) for summary in summaries)
    benchmark_mode = any(bool(summary.get("benchmark_mode")) for summary in summaries)
    target_timeouts = BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS
    return {
        "claim_set": "local_gate",
        "mode": "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "readiness": {
            "ready": all(bool(summary.get("readiness", {}).get("ready")) for summary in summaries),
            "claim_sets": {summary["claim_set"]: summary.get("readiness", {}) for summary in summaries},
        },
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "failure_counts": failure_counts,
        "claim_sets": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-set", choices=CLAIM_SETS, action="append")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--allow-unready", action="store_true")
    parser.add_argument("--benchmark-mode", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--stratified", action="store_true")
    args = parser.parse_args()

    selected = tuple(args.claim_set or CLAIM_SETS)
    summaries = [
        run_claim_set(
            claim_set,
            enforce_readiness=not args.allow_unready,
            benchmark_mode=args.benchmark_mode,
            limit=args.limit,
            stratified=args.stratified,
        )
        for claim_set in selected
    ]
    for summary in summaries:
        path = write_summary(summary["claim_set"], summary, args.output_dir)
        print(
            f"{summary['claim_set']}: pass@1={summary['pass_at_1']:.3f} "
            f"({summary['claims_passed']}/{summary['claims_total']}) -> {path}"
        )
    combined = _combine_summaries(summaries)
    combined_path = write_summary("local_gate", combined, args.output_dir)
    print(f"local_gate: pass@1={combined['pass_at_1']:.3f} ({combined['claims_passed']}/{combined['claims_total']}) -> {combined_path}")
    if not combined["readiness"]["ready"]:
        return 1
    return 0 if combined["claims_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
