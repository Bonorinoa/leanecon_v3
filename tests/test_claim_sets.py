"""Validation for curated regression claim sets."""

from __future__ import annotations

import json
from pathlib import Path

from src.lean.compiler import compile_check


def test_new_tier2_batch_theorem_stubs_are_lean_valid() -> None:
    claim_path = Path("evals/claim_sets/regressions/new_tier2_batch.jsonl")
    failures: list[str] = []
    for line in claim_path.read_text(encoding="utf-8").splitlines():
        claim = json.loads(line)
        theorem_stub = str(claim.get("theorem_stub") or "")
        result = compile_check(theorem_stub, timeout=30)
        errors = result.get("errors") or []
        if result.get("exit_code") != 0 or errors:
            failures.append(
                f"{claim.get('id')}: exit_code={result.get('exit_code')} "
                f"errors={errors or result.get('stderr') or result.get('output')}"
            )

    assert not failures
