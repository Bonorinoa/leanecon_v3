from __future__ import annotations

from pathlib import Path

import pytest

import evals.common as common


def test_claim_set_path_prefers_canonical_top_level() -> None:
    path = common.claim_set_path("tier1_core_preamble_definable")
    assert path == (Path.cwd() / "evals/claim_sets/tier1_core_preamble_definable.jsonl").resolve()


def test_claim_set_path_resolves_regression_and_archive_sets() -> None:
    assert common.claim_set_path("prover_easy_definable") == (
        Path.cwd() / "evals/claim_sets/regressions/prover_easy_definable.jsonl"
    ).resolve()
    assert common.claim_set_path("tier1_core") == (Path.cwd() / "evals/claim_sets/archive/tier1_core.jsonl").resolve()


def test_claim_set_path_rejects_unknown_name() -> None:
    with pytest.raises(FileNotFoundError):
        common.claim_set_path("definitely_missing_claim_set")
