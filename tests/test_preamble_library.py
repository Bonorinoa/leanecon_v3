from __future__ import annotations

import json

from src.preamble_library import LEAN_WORKSPACE, PREAMBLE_LIBRARY, rank_matching_preambles


def test_sprint20_preamble_entries_are_indexed() -> None:
    expected = {
        "bellman_contraction": "BellmanContractionCertificate.exists_fixedPoint",
        "contraction_mapping": "contraction_has_unique_fixedPoint",
        "kuhn_tucker": "KuhnTuckerPoint.multiplier_eq_zero_of_slack",
        "constrained_optimization": "exists_isConstrainedMaximum_of_isCompact_continuousOn",
        "monotone_sequence": "monotone_boundedAbove_converges",
    }

    for entry_name, lemma_name in expected.items():
        entry = PREAMBLE_LIBRARY[entry_name]
        assert entry.lean_path.exists()
        assert lemma_name in entry.planner_proven_lemmas


def test_sprint20_gap_queries_retrieve_new_entries() -> None:
    bellman_hits = rank_matching_preambles(
        "A Bellman-style operator with a contraction certificate has a fixed point value function",
    )
    sequence_hits = rank_matching_preambles(
        "A monotone real sequence bounded above converges",
    )

    assert any(entry.name == "bellman_contraction" for entry, _score in bellman_hits[:5])
    assert any(entry.name == "monotone_sequence" for entry, _score in sequence_hits[:5])


def test_every_registered_preamble_entry_has_existing_lean_file() -> None:
    missing = [entry.name for entry in PREAMBLE_LIBRARY.values() if not entry.lean_path.exists()]
    assert missing == []


def test_registered_definitions_and_lemmas_are_in_lean_source() -> None:
    missing: list[tuple[str, str]] = []
    for entry in PREAMBLE_LIBRARY.values():
        source = entry.lean_path.read_text(encoding="utf-8")
        for identifier in (*entry.definitions, *entry.planner_proven_lemmas):
            leaf = identifier.rsplit(".", 1)[-1]
            if identifier not in source and leaf not in source:
                missing.append((entry.name, identifier))
    assert missing == []


def test_metadata_json_files_are_accounted_for_by_registry() -> None:
    registry_by_id = {entry.name: entry for entry in PREAMBLE_LIBRARY.values()}
    metadata_paths = sorted((LEAN_WORKSPACE / "LeanEcon/Preamble").rglob("*.metadata.json"))
    assert metadata_paths

    unaccounted: list[str] = []
    mismatches: list[str] = []
    for path in metadata_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata_id = str(payload.get("id") or "")
        entry = registry_by_id.get(metadata_id)
        if entry is None:
            unaccounted.append(str(path.relative_to(LEAN_WORKSPACE)))
            continue
        expected = entry.lean_path.with_suffix(".metadata.json")
        if path != expected:
            mismatches.append(
                f"{metadata_id}: {path.relative_to(LEAN_WORKSPACE)} != {expected.relative_to(LEAN_WORKSPACE)}"
            )
    assert unaccounted == []
    assert mismatches == []


def test_metadata_json_lemmas_match_registry_or_source() -> None:
    missing: list[tuple[str, str]] = []
    for entry in PREAMBLE_LIBRARY.values():
        metadata_path = entry.lean_path.with_suffix(".metadata.json")
        if not metadata_path.exists():
            continue
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        source = entry.lean_path.read_text(encoding="utf-8")
        registry_lemmas = set(entry.planner_proven_lemmas)
        for lemma in payload.get("proven_lemmas", []):
            lemma_name = str(lemma)
            leaf = lemma_name.rsplit(".", 1)[-1]
            if lemma_name not in registry_lemmas and lemma_name not in source and leaf not in source:
                missing.append((entry.name, lemma_name))
    assert missing == []
