"""Shared benchmark helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import BENCHMARK_BASELINE_DIR, EVAL_CLAIMS_DIR

STANDARD_BENCHMARK_CLAIM_SETS = (
    "tier1_core_preamble_definable",
    "tier2_frontier_mathlib_native",
    "tier2_frontier_preamble_definable",
)
LOCAL_GATE_DEFAULT_CLAIM_SETS = ("tier0_smoke", *STANDARD_BENCHMARK_CLAIM_SETS)
LEGACY_HISTORICAL_CLAIM_SETS = ("tier1_core", "tier2_frontier")
REGRESSION_CLAIM_SETS = ("prover_easy_definable",)
ARCHIVED_UTILITY_CLAIM_SETS = (
    "sprint18_ood_temp",
    "tier1_core_sample5",
    "tier1_core_sample5_b",
    "tier2_frontier_fail9_sprint18",
    "tier2_frontier_sample3_sprint18",
)
NONCANONICAL_CLAIM_SETS = (*LEGACY_HISTORICAL_CLAIM_SETS, *REGRESSION_CLAIM_SETS, *ARCHIVED_UTILITY_CLAIM_SETS)
EXPERIMENTAL_CLAIM_SETS = (
    "phd_qual_alpha",
    *REGRESSION_CLAIM_SETS,
)


def claim_set_path(name: str) -> Path:
    candidate_dirs = (
        EVAL_CLAIMS_DIR,
        EVAL_CLAIMS_DIR / "regressions",
        EVAL_CLAIMS_DIR / "archive",
    )
    for directory in candidate_dirs:
        path = directory / f"{name}.jsonl"
        if path.exists():
            return path
    raise FileNotFoundError(f"Unknown claim set: {name}")


def load_claims(name: str) -> list[dict[str, Any]]:
    claims = []
    for line in claim_set_path(name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            claims.append(json.loads(line))
    return claims


def baseline_path(name: str, output_dir: Path | None = None) -> Path:
    directory = output_dir or BENCHMARK_BASELINE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{name}.json"


def progress_log_path(name: str, output_dir: Path | None = None) -> Path:
    directory = output_dir or BENCHMARK_BASELINE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{name}.progress.jsonl"


def reset_progress_log(name: str, output_dir: Path | None = None) -> Path:
    path = progress_log_path(name, output_dir)
    path.write_text("", encoding="utf-8")
    return path


def load_summary(name: str, output_dir: Path | None = None) -> dict[str, Any]:
    path = baseline_path(name, output_dir)
    if not path.exists():
        raise FileNotFoundError(f"Missing benchmark summary: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(name: str, payload: dict[str, Any], output_dir: Path | None = None) -> Path:
    path = baseline_path(name, output_dir)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_progress_log(name: str, events: list[dict[str, Any]], output_dir: Path | None = None) -> Path:
    path = progress_log_path(name, output_dir)
    lines = [json.dumps(event, sort_keys=True) for event in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def append_progress_event(name: str, event: dict[str, Any], output_dir: Path | None = None) -> Path:
    path = progress_log_path(name, output_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return path
