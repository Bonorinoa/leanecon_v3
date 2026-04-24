"""Post-process benchmark artifacts into actionable preamble-gap suggestions."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from src.config import BENCHMARK_BASELINE_DIR
from src.evals.metrics_aggregator import benchmark_history_path, load_history_rows

GAPS_FILENAME = "preamble_gaps.jsonl"
BENCHMARK_MODE_DIRNAME = "benchmark_mode"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")
_SKIP_IDENTIFIERS = frozenset(
    {
        "exact",
        "simpa",
        "using",
        "constructor",
        "cases",
        "have",
        "by",
        "fun",
        "let",
        "show",
        "from",
        "match",
        "at",
        "with",
        "if",
        "then",
        "else",
        "true",
        "false",
    }
)


def preamble_gaps_path(base_dir: Path | None = None) -> Path:
    root = (base_dir or BENCHMARK_BASELINE_DIR).resolve()
    return root / GAPS_FILENAME


def benchmark_mode_dir(base_dir: Path | None = None) -> Path:
    root = (base_dir or BENCHMARK_BASELINE_DIR).resolve()
    return root / BENCHMARK_MODE_DIRNAME


def load_last_run_summaries(
    *,
    base_dir: Path | None = None,
    history_path: Path | None = None,
) -> list[dict[str, Any]]:
    root = (base_dir or BENCHMARK_BASELINE_DIR).resolve()
    history_file = history_path or benchmark_history_path(root)
    rows = load_history_rows(history_file) if history_file.exists() else []
    selected_claim_sets = rows[-1].get("selected_claim_sets", []) if rows else []
    summaries_dir = benchmark_mode_dir(root)
    claim_sets = [str(name) for name in selected_claim_sets if str(name).strip()]
    if not claim_sets:
        claim_sets = sorted(
            path.stem
            for path in summaries_dir.glob("*.json")
            if path.stem not in {"local_gate"}
        )
    summaries: list[dict[str, Any]] = []
    for claim_set in claim_sets:
        path = summaries_dir / f"{claim_set}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.setdefault("claim_set", claim_set)
            summaries.append(payload)
    return summaries


def detect_gaps_from_claim_result(
    result: dict[str, Any],
    *,
    claim_set: str | None = None,
) -> list[dict[str, Any]]:
    if str(result.get("status") or "") == "verified":
        return []
    return _gap_candidates_for_result(result, claim_set=claim_set)


def detect_gaps_from_run_summary(run_summary: dict[str, Any]) -> list[dict[str, Any]]:
    summaries = run_summary.get("claim_sets")
    if isinstance(summaries, list):
        return detect_gaps_from_summaries([dict(item) for item in summaries])
    return detect_gaps_from_summaries([dict(run_summary)])


def detect_gaps_from_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        claim_set = str(summary.get("claim_set") or "")
        for result in summary.get("results", []):
            for gap in detect_gaps_from_claim_result(dict(result), claim_set=claim_set):
                existing = aggregated.get(gap["gap_id"])
                if existing is None:
                    aggregated[gap["gap_id"]] = gap
                    continue
                existing["frequency"] += 1
                existing["affected_claims"] = sorted(
                    {*(existing.get("affected_claims") or []), *(gap.get("affected_claims") or [])}
                )
                existing["priority_score"] = round(
                    float(existing["frequency"]) * float(existing["_difficulty"]),
                    3,
                )
    ranked = sorted(
        aggregated.values(),
        key=lambda gap: (-float(gap["priority_score"]), gap["gap_id"]),
    )
    for gap in ranked:
        gap.pop("_difficulty", None)
    return ranked


def append_gaps_jsonl(
    gaps: list[dict[str, Any]],
    *,
    output_path: Path | None = None,
) -> Path:
    path = output_path or preamble_gaps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for gap in gaps:
            handle.write(json.dumps(gap, sort_keys=True) + "\n")
    return path


def _gap_candidates_for_result(result: dict[str, Any], *, claim_set: str | None) -> list[dict[str, Any]]:
    claim_id = str(result.get("id") or "unknown_claim")
    claim_text = str(result.get("raw_claim") or "")
    bucket = str(result.get("benchmark_bucket") or "")
    termination_reason = str(result.get("termination_reason") or "")
    failure_code = str(result.get("failure_code") or "")
    attempts = _direct_close_attempts(result)
    identifiers = _attempt_identifiers(attempts)
    target_names = _target_names(result)
    tool_patterns = _tool_patterns(result)
    claim_blob = f"{claim_text} {' '.join(target_names)} {' '.join(identifiers)}".lower()

    heuristic = _select_heuristic(
        claim_blob=claim_blob,
        claim_text=claim_text,
        target_names=target_names,
        identifiers=identifiers,
        bucket=bucket,
    )
    if heuristic is None:
        heuristic = _fallback_gap_spec(
            claim_id=claim_id,
            claim_text=claim_text,
            target_names=target_names,
            identifiers=identifiers,
            bucket=bucket,
        )

    rationale = (
        f"{heuristic['rationale']} "
        f"Observed `{failure_code or 'unknown_failure'}` / `{termination_reason or 'unknown_termination'}` "
        f"on claim `{claim_id}`"
    )
    if claim_set:
        rationale += f" in `{claim_set}`"
    if attempts:
        attempt_sources = ", ".join(sorted({str(item['source']) for item in attempts if item.get('source')}))
        rationale += f" after {len(attempts)} failed direct-close attempts"
        if attempt_sources:
            rationale += f" sourced from {attempt_sources}"
    if identifiers:
        rationale += f"; attempted identifiers included {', '.join(identifiers[:4])}"
    if tool_patterns:
        rationale += f"; post-shortcut tool pattern: {', '.join(tool_patterns[:3])}"
    rationale += "."

    gap = {
        "gap_id": heuristic["gap_id"],
        "frequency": 1,
        "affected_claims": [claim_id],
        "suggested_lean_stub": heuristic["suggested_lean_stub"],
        "priority_score": round(float(heuristic["difficulty"]), 3),
        "rationale": rationale,
        "_difficulty": float(heuristic["difficulty"]),
    }
    return [gap]


def _select_heuristic(
    *,
    claim_blob: str,
    claim_text: str,
    target_names: list[str],
    identifiers: list[str],
    bucket: str,
) -> dict[str, Any] | None:
    identifier_blob = " ".join(identifiers)
    target_blob = " ".join(target_names).lower()

    if (
        "continuous utility representation remains continuous" in claim_blob
        or "continuouspreference_continuouson" in claim_blob
        or "h_continuous_preference" in target_blob
    ):
        return {
            "gap_id": "continuous_preference.restriction_continuity",
            "difficulty": 5.0,
            "suggested_lean_stub": (
                "theorem continuousPreference_continuousOn_subset\n"
                "    {α : Type*} [TopologicalSpace α] [TopologicalSpace ℝ]\n"
                "    {u : α → ℝ} (hu : ContinuousPreference u) (s : Set α) :\n"
                "    ContinuousOn u s := by\n"
                "  sorry"
            ),
            "rationale": (
                "The direct-close search already probed `hu.continuousOn` and "
                "`continuousPreference_continuousOn`, so the failure looks like a missing "
                "restriction bridge rather than a search problem."
            ),
        }

    if (
        "bellman-style operator" in claim_blob
        and "contraction" in claim_blob
        or "h_bellman_contraction" in target_blob
        or "contraction_has_fixedpoint" in claim_blob
    ):
        return {
            "gap_id": "dynamic_programming.bellman_operator_contraction_bridge",
            "difficulty": 4.8,
            "suggested_lean_stub": (
                "structure BellmanContractionCertificate {V : Type*} [MetricSpace V] (T : V → V) where\n"
                "  constant : NNReal\n"
                "  contracting : ContractingWith constant T\n\n"
                "theorem BellmanContractionCertificate.exists_fixedPoint\n"
                "    {V : Type*} [MetricSpace V] [CompleteSpace V] [Nonempty V]\n"
                "    {T : V → V} (h : BellmanContractionCertificate T) :\n"
                "    ∃ v, Function.IsFixedPt T v := by\n"
                "  exact contraction_has_fixedPoint h.isContraction"
            ),
            "rationale": (
                "This claim is trying to turn Bellman assumptions into the contraction template. "
                "The attempted identifiers show the prover jumping between `BellmanOperator.monotone` "
                "and `contraction_has_fixedPoint` without a certificate that packages the metric estimate."
            ),
        }

    if (
        "kuhn-tucker" in claim_blob
        or "complementary slackness" in claim_blob
        or "h_slackness" in target_blob
        or "h_vanish" in target_blob
        or "kuhntuckerpoint.complementary_slackness" in claim_blob
    ):
        return {
            "gap_id": "optimization.kuhn_tucker_multiplier_zero_of_slack",
            "difficulty": 4.7,
            "suggested_lean_stub": (
                "theorem KuhnTuckerPoint.multiplier_eq_zero_of_slack\n"
                "    {α ι : Type*} {x : α} {g : α → ι → ℝ} {μ : ι → ℝ}\n"
                "    (hkt : KuhnTuckerPoint x g μ) {i : ι}\n"
                "    (hslack : g x i < 0) :\n"
                "    μ i = 0 := by\n"
                "  have hg_ne : g x i ≠ 0 := ne_of_lt hslack\n"
                "  exact eq_zero_of_ne_zero_of_mul_right_eq_zero hg_ne (hkt.slackness i)"
            ),
            "rationale": (
                "The trace reaches complementary-slackness facts but still cannot directly close the "
                "vanishing-multiplier goal, which suggests the preamble needs the stronger zero-of-slack corollary."
            ),
        }

    if (
        "strictly concave function attains a maximum on a compact set" in claim_blob
        or ("compact" in claim_blob and "maximum" in claim_blob and "h_existence" in target_blob)
    ):
        return {
            "gap_id": "optimization.strict_concavity_compact_attains_maximum",
            "difficulty": 4.4,
            "suggested_lean_stub": (
                "theorem exists_isConstrainedMaximum_of_isCompact_continuousOn\n"
                "    {α : Type*} [TopologicalSpace α]\n"
                "    {s : Set α} {f : α → ℝ}\n"
                "    (hs : IsCompact s) (hne : s.Nonempty)\n"
                "    (hf : ContinuousOn f s) :\n"
                "    ∃ x, IsConstrainedMaximum f s x := by\n"
                "  rcases hs.exists_isMaxOn hne hf with ⟨x, hx, hmax⟩\n"
                "  exact ⟨x, hx, fun {_} hy => hmax hy⟩"
            ),
            "rationale": (
                "The direct-close attempts bounced between fixed-point and constrained-optimization lemmas, "
                "but the target is existence of a maximizer. A compact-attainment wrapper around Mathlib's "
                "extreme-value theorem would have been a natural direct-close."
            ),
        }

    if (
        "monotone sequence bounded above converges" in claim_blob
        or "h_monotone_sequence" in target_blob
        or ("monotone sequence" in claim_blob and bucket == "mathlib_native")
    ):
        return {
            "gap_id": "analysis.monotone_bounded_sequence_converges",
            "difficulty": 4.2,
            "suggested_lean_stub": (
                "theorem monotone_boundedAbove_converges\n"
                "    {u : ℕ → ℝ}\n"
                "    (hu_mono : Monotone u)\n"
                "    (hu_bdd : BddAbove (Set.range u)) :\n"
                "    ∃ l, Filter.Tendsto u Filter.atTop (nhds l) := by\n"
                "  exact ⟨⨆ i, u i, tendsto_atTop_ciSup hu_mono hu_bdd⟩"
            ),
            "rationale": (
                "The mathlib-native analysis claim never finds the convergence bridge and instead drifts through unrelated direct-close families. "
                "A canonical monotone-plus-bounded convergence wrapper would make the intended theorem template explicit."
            ),
        }

    if "unique fixed point" in claim_blob or "h_contraction_fixed_point" in target_blob:
        return {
            "gap_id": "dynamic_programming.contraction_unique_fixed_point",
            "difficulty": 4.0,
            "suggested_lean_stub": (
                "theorem contraction_has_unique_fixedPoint {α : Type*}\n"
                "    [MetricSpace α] [CompleteSpace α] [Nonempty α]\n"
                "    {f : α → α} (hf : IsContraction f) :\n"
                "    ∃! x, Function.IsFixedPt f x := by\n"
                "  rcases hf with ⟨K, hK⟩\n"
                "  refine ⟨ContractingWith.fixedPoint (f := f) hK, ?_, ?_⟩\n"
                "  · exact ContractingWith.fixedPoint_isFixedPt (f := f) hK\n"
                "  · intro y hy\n"
                "    exact ContractingWith.fixedPoint_unique hK hy"
            ),
            "rationale": (
                "The target asks for the uniqueness side of the contraction-mapping theorem, "
                "not just fixed-point existence."
            ),
        }

    if "witness profile" in claim_blob or "h_witness_exists" in target_blob:
        return {
            "gap_id": "game_theory.nash_witness_existence",
            "difficulty": 3.6,
            "suggested_lean_stub": (
                "theorem nash_exists_of_witness {Profile : Type}\n"
                "    (h : HasNashEquilibrium Profile) : ∃ profile, h.isNash profile := by\n"
                "  exact ⟨h.witness, h.is_nash⟩"
            ),
            "rationale": (
                "The trace already has a witness-style Nash certificate; the closure should use "
                "`nash_exists_of_witness` directly instead of drifting through fixed-point lemmas."
            ),
        }

    return None


def _fallback_gap_spec(
    *,
    claim_id: str,
    claim_text: str,
    target_names: list[str],
    identifiers: list[str],
    bucket: str,
) -> dict[str, Any]:
    target_hint = target_names[0] if target_names else claim_id
    sanitized_target = re.sub(r"[^A-Za-z0-9_]+", "_", target_hint).strip("_") or "gap_target"
    identifier_hint = identifiers[0] if identifiers else "missing_bridge"
    sanitized_identifier = re.sub(r"[^A-Za-z0-9_]+", "_", identifier_hint).strip("_") or "missing_bridge"
    return {
        "gap_id": f"generic.{sanitized_target.lower()}",
        "difficulty": 3.5 if bucket == "preamble_definable" else 3.8,
        "suggested_lean_stub": (
            f"theorem {sanitized_identifier}_for_{sanitized_target}\n"
            "    {α : Type*} :\n"
            "    Prop := by\n"
            "  sorry"
        ),
        "rationale": (
            f"No specialized detector matched claim `{claim_text or claim_id}`. "
            "This fallback stub captures the main target/identifier pair so the missing bridge can still be reviewed."
        ),
    }


def _direct_close_attempts(result: dict[str, Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for event in result.get("progress_events", []):
        message = str(event.get("message") or "")
        if not message.startswith("Direct closure attempt"):
            continue
        metadata = event.get("metadata") or {}
        attempts.append(
            {
                "proof": str(metadata.get("proof") or ""),
                "source": str(metadata.get("source") or ""),
                "target_name": str(metadata.get("target_name") or ""),
            }
        )
    return attempts


def _target_names(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for event in result.get("progress_events", []):
        metadata = event.get("metadata") or {}
        target_name = str(metadata.get("target_name") or "").strip()
        if target_name and target_name not in names:
            names.append(target_name)
    return names


def _tool_patterns(result: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for event in result.get("progress_events", []):
        if str(event.get("event") or "") != "prover_tool":
            continue
        message = str(event.get("message") or "")
        if not message.startswith("Tool `"):
            continue
        metadata = event.get("metadata") or {}
        tool_name = str(metadata.get("tool_name") or "")
        success = bool(metadata.get("success"))
        error_code = str(metadata.get("error_code") or "")
        fragment = f"{tool_name}:{'success' if success else 'failed'}"
        if error_code:
            fragment += f":{error_code}"
        if fragment not in patterns:
            patterns.append(fragment)
    return patterns


def _attempt_identifiers(attempts: list[dict[str, Any]]) -> list[str]:
    counter: Counter[str] = Counter()
    for attempt in attempts:
        proof = str(attempt.get("proof") or "")
        for match in _IDENTIFIER_RE.findall(proof):
            if match in _SKIP_IDENTIFIERS:
                continue
            if len(match) <= 1:
                continue
            counter[match] += 1
    return [identifier for identifier, _count in counter.most_common()]
