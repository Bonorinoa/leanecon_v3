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
    assert len(lines) >= 300, f"seed too small: {len(lines)} entries"
    sample = json.loads(lines[0])
    for key in ("name", "type_signature", "tags"):
        assert key in sample, f"seed missing key {key!r}"


def _seed_names() -> set[str]:
    return {
        json.loads(line)["name"]
        for line in DEFAULT_SEED_PATH.read_text().splitlines()
        if line.strip()
    }


def test_seed_covers_extreme_value_domain() -> None:
    """Sprint 23: extractor must seed the extreme value theorem family."""
    names = _seed_names()
    assert "IsCompact.exists_isMaxOn" in names, "missing core extreme value premise"
    related = {
        n
        for n in names
        if "exists_isMaxOn" in n or "exists_isMinOn" in n or "exists_isLeast" in n
    }
    assert len(related) >= 2, f"too few extreme value premises: {related}"


def test_seed_covers_monotone_convergence_domain() -> None:
    """Sprint 23: extractor must seed monotone-convergence-style premises."""
    names = _seed_names()
    monotone_convergence = {
        n
        for n in names
        if "tendsto_atTop_of_monotone" in n
        or ("Monotone" in n and "tendsto" in n.lower())
        or "MonotoneConvergence" in n
    }
    assert (
        "tendsto_atTop_of_monotone" in names or monotone_convergence
    ), f"no monotone-convergence premise in seed: {monotone_convergence}"
    bounded_or_monotone = {n for n in names if "Monotone" in n or "bounded" in n.lower()}
    assert len(bounded_or_monotone) >= 2, (
        f"too few bounded/monotone premises in seed: {bounded_or_monotone}"
    )


def test_retrieve_extreme_value_returns_relevant(rag: MathlibRAG) -> None:
    """A Lean-syntax extreme-value goal should surface IsCompact.exists_isMaxOn in top-5.

    The harness feeds the RAG actual Lean goal-state text, not natural language —
    name-token Jaccard dominates the score, so the query must share decl-name
    vocabulary with the target premise.
    """
    goal = "IsCompact s → ContinuousOn f s → ∃ x ∈ s, IsMaxOn f s x"
    hits = rag.retrieve_premises(goal, k=5)
    names = [p.name for p in hits]
    assert "IsCompact.exists_isMaxOn" in names, (
        f"extreme value premise not in top-5: {names}"
    )


def test_retrieve_monotone_convergence_returns_relevant(rag: MathlibRAG) -> None:
    """A Lean-syntax monotone-convergence goal should surface ≥1 relevant tendsto/Monotone premise."""
    goal = "Monotone f → Tendsto f atTop atTop ∨ ∃ l, Tendsto f atTop (𝓝 l)"
    hits = rag.retrieve_premises(goal, k=5)
    names = [p.name for p in hits]
    relevant = [
        n
        for n in names
        if "tendsto" in n.lower() and ("monotone" in n.lower() or "atTop" in n)
    ]
    assert relevant, f"no monotone-convergence-relevant premise in top-5: {names}"


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


def test_retrieval_event_has_query_field() -> None:
    from src.observability.models import RetrievalEvent

    evt = RetrievalEvent(source="lean_leansearch", query="continuous add")
    d = evt.to_dict()
    assert d["query"] == "continuous add"
    assert d["source"] == "lean_leansearch"
    evt2 = RetrievalEvent()
    assert evt2.to_dict()["query"] is None


def test_is_stale_detects_seed_content_change_even_with_older_mtime(tmp_path: Path) -> None:
    """Sprint 24: hash-based invalidation triggers a rebuild after a seed
    content change that mtime alone would miss (e.g. after a ``git reset``)."""
    import os

    seed = tmp_path / "seed.jsonl"
    cache = tmp_path / "cache.jsonl"
    seed.write_text('{"name": "Foo.bar", "tags": ["x"]}\n', encoding="utf-8")

    rag = MathlibRAG(seed_path=seed, index_path=cache)
    rag.rebuild()
    assert cache.exists()
    assert (cache.with_suffix(cache.suffix + ".sha256")).exists()
    assert rag.is_stale() is False

    # Change content but force seed mtime to be OLDER than cache mtime so the
    # plain mtime check would say "fresh".
    seed.write_text('{"name": "Foo.baz", "tags": ["x"]}\n', encoding="utf-8")
    cache_mtime = cache.stat().st_mtime
    os.utime(seed, (cache_mtime - 10, cache_mtime - 10))

    assert rag.is_stale() is True, "content-hash check should detect the seed change"


def test_is_stale_treats_legacy_cache_without_sidecar_as_stale(tmp_path: Path) -> None:
    """A pre-Sprint-24 cache (no .sha256 sidecar) should rebuild on next access."""
    seed = tmp_path / "seed.jsonl"
    cache = tmp_path / "cache.jsonl"
    seed.write_text('{"name": "Foo.bar"}\n', encoding="utf-8")
    # Simulate a legacy cache by copying the seed without writing a sidecar.
    cache.write_bytes(seed.read_bytes())

    rag = MathlibRAG(seed_path=seed, index_path=cache)
    assert rag.is_stale() is True


def test_default_rag_uses_get_default_embedder() -> None:
    import src.retrieval.mathlib_rag as rag_mod

    rag_mod._DEFAULT_RAG = None
    rag = rag_mod._default_rag()
    from src.planner.retrieval import get_default_embedder

    expected_type = type(get_default_embedder())
    assert isinstance(rag._embedder, expected_type), (
        f"Expected {expected_type.__name__}, got {type(rag._embedder).__name__}"
    )
    rag_mod._DEFAULT_RAG = None
