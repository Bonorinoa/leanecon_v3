from __future__ import annotations

from src.preamble_library import PREAMBLE_LIBRARY, rank_matching_preambles


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
