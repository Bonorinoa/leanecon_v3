"""CLI for the benchmark preamble-gap detector."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.config import BENCHMARK_BASELINE_DIR
from src.evals.gap_detector import append_gaps_jsonl, detect_gaps_from_summaries, load_last_run_summaries, preamble_gaps_path


def _render_gap(index: int, gap: dict[str, object]) -> str:
    claims = ", ".join(str(claim) for claim in gap.get("affected_claims", []))
    return "\n".join(
        [
            f"{index}. {gap['gap_id']}  score={gap['priority_score']}",
            f"claims: {claims}",
            "stub:",
            str(gap["suggested_lean_stub"]),
            f"why: {gap['rationale']}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--last-run", action="store_true")
    parser.add_argument("--base-dir", type=Path, default=BENCHMARK_BASELINE_DIR)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args(argv)

    summaries = load_last_run_summaries(base_dir=args.base_dir)
    gaps = detect_gaps_from_summaries(summaries)
    output_path = append_gaps_jsonl(gaps, output_path=preamble_gaps_path(args.base_dir))

    if not gaps:
        print("No gaps detected.")
        print(f"Output: {output_path}")
        return 0

    print(f"Detected {len(gaps)} gaps")
    print(f"Output: {output_path}")
    for index, gap in enumerate(gaps[: max(args.top, 0)], start=1):
        print()
        print(_render_gap(index, gap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
