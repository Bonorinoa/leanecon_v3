from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.retrieval.mathlib_rag import (
    DEFAULT_SEED_PATH,
    MathlibRAG,
    Premise,
    retrieve_premises,
)


@pytest.fixture()
def rag(tmp_path: Path) -> MathlibRAG:
    # Use the repo seed but redirect the cache to tmp so tests never mutate the workspace.
    return MathlibRAG(index_path=tmp_path / "mathlib_rag.jsonl")


def test_seed_file_shape() -> None:
    assert DEFAULT_SEED_PATH.exists(), "seed JSONL missing"
    lines = [line for line in DEFAULT_SEED_PATH.read_text().splitlines() if line.strip()]
    assert len(lines) >= 30, f"seed too small: {len(lines)} entries"
    sample = json.loads(lines[0])
    for key in ("name", "type_signature", "tags"):
        assert key in sample, f"seed missing key {key!r}"


def test_retrieve_continuous_add_smoke(rag: MathlibRAG) -> None:
    goal = "theorem h : Continuous f → Continuous (λ x, f x + g x)"
    hits = rag.retrieve_premises(goal, k=5)
    assert len(hits) >= 3
    assert hits[0].score >= 0.75
    assert hits[0].name == "Continuous.add"


def test_retrieve_ranks_mul_over_add_for_product_goal(rag: MathlibRAG) -> None:
    goal = "theorem h : Continuous f → Continuous g → Continuous (fun x => f x * g x)"
    hits = rag.retrieve_premises(goal, k=5)
    names = [p.name for p in hits]
    assert "Continuous.mul" in names
    assert (
        names.index("Continuous.mul") <= names.index("Continuous.add")
        if "Continuous.add" in names
        else True
    )


def test_retrieve_honors_k(rag: MathlibRAG) -> None:
    goal = "Continuous (λ x, f x + g x)"
    hits = rag.retrieve_premises(goal, k=2)
    assert len(hits) <= 2


def test_premise_to_dict_round_trip() -> None:
    p = Premise(
        name="Continuous.add",
        score=0.91,
        statement="Continuous f → Continuous g → Continuous (f + g)",
        docstring="Pointwise sum of continuous functions.",
        file_path="Mathlib/Topology/Algebra/Monoid.lean",
        tags=("continuous", "add"),
        dependencies=("Continuous",),
    )
    payload = p.to_dict()
    assert payload["name"] == "Continuous.add"
    assert payload["score"] == 0.91
    assert payload["tags"] == ("continuous", "add")


def test_module_level_retrieve_premises_returns_list() -> None:
    results = retrieve_premises("Continuous (λ x, f x + g x)", k=3)
    assert isinstance(results, list)
    assert all(isinstance(p, Premise) for p in results)


def test_rebuild_is_idempotent(rag: MathlibRAG, tmp_path: Path) -> None:
    rag.rebuild()
    rag.rebuild()
    # After rebuild the cache file should exist and be readable
    assert rag.index_path.exists()
    first_size = rag.index_path.stat().st_size
    rag.rebuild()
    assert rag.index_path.stat().st_size == first_size


def test_empty_seed_returns_no_results(tmp_path: Path) -> None:
    empty_seed = tmp_path / "empty_seed.jsonl"
    empty_seed.write_text("")
    rag = MathlibRAG(seed_path=empty_seed, index_path=tmp_path / "cache.jsonl")
    assert rag.retrieve_premises("anything", k=5) == []
