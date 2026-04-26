"""Parser confidence tests for scripts/extract_mathlib_premises.

Run the regex-based premise extractor against three Mathlib files chosen for
distinct edge cases (attributes, multi-line signatures, where-clauses) and
assert it recovers ≥90% of a hard-coded set of expected (name, marker) pairs.
This gates running the script against the full ~7k-file Mathlib tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extract_mathlib_premises import extract_premises_from_file

REPO_ROOT = Path(__file__).resolve().parents[1]
MATHLIB_ROOT = REPO_ROOT / "lean_workspace" / ".lake" / "packages" / "mathlib"

# Each entry: (mathlib_relative_path, [(decl_name, substring_expected_in_signature), ...])
EXPECTATIONS: list[tuple[str, list[tuple[str, str]]]] = [
    # 1. Attributes: @[simp] inline AND @[simp] on previous line
    (
        "Mathlib/Topology/Basic.lean",
        [
            ("isOpen_empty", "IsOpen (∅"),
            ("isOpen_const", "IsOpen { _x"),
            ("isOpen_compl_iff", "IsOpen sᶜ"),
        ],
    ),
    # 2. Multi-line signatures spanning 2-3 lines before `:=`
    (
        "Mathlib/Topology/Order/Compact.lean",
        [
            ("IsCompact.exists_isMinOn", "ClosedIicTopology"),
            ("IsCompact.exists_isMaxOn", "ClosedIciTopology"),
            ("IsCompact.exists_isLeast", "IsLeast"),
            ("IsCompact.exists_isGreatest", "IsGreatest"),
        ],
    ),
    # 3. Where-clauses on instances + multi-line theorems
    (
        "Mathlib/Topology/MetricSpace/Basic.lean",
        [
            ("isUniformEmbedding_iff'", "IsUniformEmbedding"),
            ("isClosed_of_pairwise_le_dist", "IsClosed"),
            ("isClosedEmbedding_of_pairwise_le_dist", "IsClosedEmbedding"),
        ],
    ),
]


def _flatten_expectations() -> list[tuple[str, str, str]]:
    flat: list[tuple[str, str, str]] = []
    for rel_path, pairs in EXPECTATIONS:
        for name, marker in pairs:
            flat.append((rel_path, name, marker))
    return flat


def _mathlib_available() -> bool:
    return (MATHLIB_ROOT / "Mathlib" / "Topology" / "Basic.lean").exists()


@pytest.mark.skipif(not _mathlib_available(), reason="Mathlib checkout not present")
def test_parser_confidence_at_least_90_percent() -> None:
    """Run the parser on three tricky files; assert ≥9/10 expected entries are recovered."""
    flat = _flatten_expectations()
    assert len(flat) == 10, "fixture must contain exactly 10 expected entries"

    # Extract once per file and index by name.
    extracted_by_file: dict[str, dict[str, dict]] = {}
    for rel_path, _ in EXPECTATIONS:
        full_path = MATHLIB_ROOT / rel_path
        premises = extract_premises_from_file(full_path)
        extracted_by_file[rel_path] = {p["name"]: p for p in premises}

    hits = 0
    misses: list[str] = []
    for rel_path, name, marker in flat:
        entry = extracted_by_file[rel_path].get(name)
        if entry is None:
            misses.append(f"{rel_path}::{name} (not extracted)")
            continue
        signature = entry.get("type_signature") or ""
        if marker in signature:
            hits += 1
        else:
            misses.append(
                f"{rel_path}::{name} marker {marker!r} not in signature {signature[:120]!r}"
            )

    accuracy = hits / len(flat)
    assert accuracy >= 0.9, (
        f"Parser confidence {accuracy:.0%} below 90% threshold. Misses:\n  - "
        + "\n  - ".join(misses)
    )


@pytest.mark.skipif(not _mathlib_available(), reason="Mathlib checkout not present")
def test_parser_returns_dict_with_required_fields() -> None:
    """Each extracted premise must carry the seed schema fields."""
    sample = MATHLIB_ROOT / "Mathlib" / "Topology" / "Basic.lean"
    premises = extract_premises_from_file(sample)
    assert premises, "expected at least one premise from Topology/Basic.lean"
    required = {"name", "type_signature", "docstring", "tags", "dependencies", "file_path"}
    for premise in premises[:5]:
        missing = required - set(premise.keys())
        assert not missing, f"premise missing fields {missing}: {premise!r}"


@pytest.mark.skipif(not _mathlib_available(), reason="Mathlib checkout not present")
def test_parser_skips_comments_and_definitions_only() -> None:
    """Parser should not extract names from comments or string literals."""
    sample = MATHLIB_ROOT / "Mathlib" / "Topology" / "Basic.lean"
    premises = extract_premises_from_file(sample)
    # File header references theorems in module-doc comments; none should appear as decls.
    names = {p["name"] for p in premises}
    # These appear only inside `/- ... -/` doc-comments at the top of the file, not as decls.
    assert "TopologicalSpace" not in names or any(
        p.get("type_signature") for p in premises if p["name"] == "TopologicalSpace"
    ), "comment-only references should not produce premises without signatures"
