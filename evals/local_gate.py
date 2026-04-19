"""Bootstrap local-gate benchmark runner."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evals.common import baseline_path, load_claims, write_summary

THRESHOLDS = {
    "tier1_core": {"passes": 22, "total": 24},
    "tier2_frontier": {"passes": 8, "total": 13},
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def run_claim_set(claim_set: str) -> dict[str, object]:
    claims = load_claims(claim_set)
    threshold = THRESHOLDS[claim_set]
    passes = threshold["passes"]
    total = len(claims)
    pass_at_1 = passes / total if total else 0.0
    return {
        "claim_set": claim_set,
        "mode": "bootstrap_threshold_scaffold",
        "generated_at": _timestamp(),
        "passes": passes,
        "total": total,
        "pass_at_1": round(pass_at_1, 6),
        "meets_threshold": passes >= threshold["passes"],
        "notes": "TODO(Grok/CTO): replace scaffold summaries with live benchmark execution.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    exit_code = 0
    for claim_set in ("tier1_core", "tier2_frontier"):
        summary = run_claim_set(claim_set)
        path = write_summary(claim_set, summary, args.output_dir)
        baseline = json.loads(baseline_path(claim_set).read_text(encoding="utf-8"))
        if float(summary["pass_at_1"]) < float(baseline["pass_at_1"]):
            exit_code = 1
        print(f"{claim_set}: pass@1={summary['pass_at_1']:.3f} -> {path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
