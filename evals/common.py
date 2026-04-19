"""Shared benchmark helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import BENCHMARK_BASELINE_DIR, EVAL_CLAIMS_DIR


def claim_set_path(name: str) -> Path:
    path = EVAL_CLAIMS_DIR / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Unknown claim set: {name}")
    return path


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


def write_summary(name: str, payload: dict[str, Any], output_dir: Path | None = None) -> Path:
    path = baseline_path(name, output_dir)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
