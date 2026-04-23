"""Benchmark integrity manifests and claim-bucket summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.common import EVAL_CLAIMS_DIR, LEGACY_HISTORICAL_CLAIM_SETS, STANDARD_BENCHMARK_CLAIM_SETS, load_claims

BENCHMARK_BUCKETS = frozenset(
    {
        "preamble_definable",
        "mathlib_native",
        "planner_formalizer",
        "prover_search",
        "regression",
    }
)
BUCKET_MAP_PATH = Path(__file__).resolve().parent / "claim_buckets.json"
MANIFEST_PATH = Path(__file__).resolve().parent / "benchmark_manifest.json"
LEGACY_MIXED_SETS = frozenset(LEGACY_HISTORICAL_CLAIM_SETS)


def load_bucket_map() -> dict[str, str]:
    return json.loads(BUCKET_MAP_PATH.read_text(encoding="utf-8"))


def classify_claim(claim: dict[str, Any], bucket_map: dict[str, str] | None = None) -> str:
    claim_id = str(claim["id"])
    buckets = bucket_map or load_bucket_map()
    bucket = buckets.get(claim_id)
    if bucket not in BENCHMARK_BUCKETS:
        raise ValueError(f"Claim `{claim_id}` is missing a valid benchmark bucket.")
    return bucket


def build_claim_set_manifest(name: str, *, bucket_map: dict[str, str] | None = None) -> dict[str, Any]:
    buckets = bucket_map or load_bucket_map()
    claims = load_claims(name)
    bucket_counts = {bucket: 0 for bucket in BENCHMARK_BUCKETS}
    expected_category_counts: dict[str, int] = {}
    theorem_stub_count = 0
    pinned_preamble_count = 0
    claim_records: list[dict[str, Any]] = []
    for claim in claims:
        bucket = classify_claim(claim, buckets)
        bucket_counts[bucket] += 1
        expected_category = str(claim.get("expected_category") or "UNKNOWN")
        expected_category_counts[expected_category] = expected_category_counts.get(expected_category, 0) + 1
        theorem_stub_present = bool(claim.get("theorem_stub"))
        preamble_pinned = bool(claim.get("preamble_names"))
        theorem_stub_count += 1 if theorem_stub_present else 0
        pinned_preamble_count += 1 if preamble_pinned else 0
        claim_records.append(
            {
                "id": str(claim["id"]),
                "bucket": bucket,
                "expected_category": expected_category,
                "theorem_stub_present": theorem_stub_present,
                "preamble_pinned": preamble_pinned,
            }
        )
    integrity_status = "mixed_historical" if name in LEGACY_MIXED_SETS else "focused"
    return {
        "claim_set": name,
        "integrity_status": integrity_status,
        "claims_total": len(claims),
        "bucket_counts": bucket_counts,
        "expected_category_counts": expected_category_counts,
        "theorem_stub_count": theorem_stub_count,
        "pinned_preamble_count": pinned_preamble_count,
        "claim_records": claim_records,
    }


def build_manifest(*, include_standard_only: bool = True) -> dict[str, Any]:
    bucket_map = load_bucket_map()
    claim_set_paths = sorted(EVAL_CLAIMS_DIR.glob("*.jsonl"))
    selected_names = (
        list(STANDARD_BENCHMARK_CLAIM_SETS)
        if include_standard_only
        else [path.stem for path in claim_set_paths]
    )
    claim_sets = [build_claim_set_manifest(name, bucket_map=bucket_map) for name in selected_names]
    aggregate_bucket_counts = {bucket: 0 for bucket in BENCHMARK_BUCKETS}
    for claim_set in claim_sets:
        for bucket, count in claim_set["bucket_counts"].items():
            aggregate_bucket_counts[bucket] += int(count)
    return {
        "version": 1,
        "notes": {
            "public_score_ready": False,
            "historical_artifacts_only": True,
            "legacy_mixed_sets": sorted(LEGACY_MIXED_SETS),
        },
        "aggregate_bucket_counts": aggregate_bucket_counts,
        "claim_sets": claim_sets,
    }


def write_manifest(path: Path = MANIFEST_PATH) -> Path:
    path.write_text(json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
