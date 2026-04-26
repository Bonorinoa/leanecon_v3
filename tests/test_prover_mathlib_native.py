"""Sprint 23 Task 2 + 3 — focused tests for the mathlib-native prover harness.

Covers the LeanSearch hover-enrichment path (Task 2), the second-retrieval
stall recovery + decomposition hint (Task 3), and the hybrid budget bump.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.observability.tool_tracker import BudgetTracker
from src.prover.prover import Prover


class _ScriptedLSPClient:
    """Minimal LSP stub that returns canonical leansearch + outline + hover payloads."""

    def __init__(
        self,
        *,
        leansearch_items: list[dict[str, Any]] | None = None,
        outline_by_path: dict[str, list[dict[str, Any]]] | None = None,
        hover_by_position: dict[tuple[str, int, int], str] | None = None,
    ) -> None:
        self.leansearch_items = leansearch_items or []
        self.outline_by_path = outline_by_path or {}
        self.hover_by_position = hover_by_position or {}
        self.outline_calls = 0
        self.hover_calls = 0

    def lean_leansearch(self, query: str, *, num_results: int = 8):
        return {"items": list(self.leansearch_items[:num_results])}

    def lean_file_outline(self, file_path: Path | str, *, max_declarations: int | None = None):
        self.outline_calls += 1
        key = str(file_path)
        return {"declarations": list(self.outline_by_path.get(key, []))}

    def lean_hover_info(self, file_path: Path | str, *, line: int, column: int):
        self.hover_calls += 1
        contents = self.hover_by_position.get((str(file_path), int(line), int(column)), "")
        return {"contents": contents}

    # No-op stubs so unrelated harness paths don't crash if accidentally invoked.
    def lean_diagnostic_messages(self, *args, **kwargs):
        return {"items": []}

    def lean_goal(self, *args, **kwargs):
        return {"goals_after": []}

    def lean_code_actions(self, *args, **kwargs):
        return {"items": []}


def _make_prover(lsp_client) -> Prover:
    """Construct a minimally-configured Prover using only stub drivers."""
    from tests.test_prover import ScriptedDriver

    return Prover(
        huggingface_driver=ScriptedDriver({}),
        mistral_driver=ScriptedDriver({}),
        lsp_client=lsp_client,
        budget_tracker=BudgetTracker(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Task 2 — LeanSearch enrichment via outline + hover
# ──────────────────────────────────────────────────────────────────────────────


def test_leansearch_premises_get_enriched_via_outline_plus_hover() -> None:
    """When leansearch returns a name + module, the prover fetches outline+hover and enriches."""
    module_path = "Mathlib/Topology/Order/Compact.lean"
    full_signature = (
        "[ClosedIciTopology α] {s : Set β} (hs : IsCompact s) (ne_s : s.Nonempty) "
        "{f : β → α} (hf : ContinuousOn f s) : ∃ x ∈ s, IsMaxOn f s x"
    )
    lsp = _ScriptedLSPClient(
        leansearch_items=[
            {
                "name": "IsCompact.exists_isMaxOn",
                "module": module_path,
                "type": "thin",
                "docstring": "thin",
            }
        ],
        outline_by_path={
            module_path: [
                {"name": "IsCompact.exists_isMaxOn", "line": 246, "column": 0}
            ]
        },
        hover_by_position={
            (module_path, 246, 0): full_signature,
        },
    )
    prover = _make_prover(lsp)

    event = prover._retrieve_lean_search_premises("extreme value theorem", k=5)

    assert len(event.retrieved_premises) == 1
    premise = event.retrieved_premises[0]
    assert premise.get("full_type_signature"), "expected enriched full_type_signature"
    assert "IsMaxOn" in premise["full_type_signature"]
    assert premise.get("declaration_location"), "expected declaration_location"
    payload = event.to_dict()
    assert payload.get("enriched_count") == 1, f"missing enriched_count: {payload}"


def test_leansearch_enrichment_skips_when_outline_missing_decl() -> None:
    """Missing outline entry must not fail retrieval — premise is returned without enrichment."""
    module_path = "Mathlib/Some/Other.lean"
    lsp = _ScriptedLSPClient(
        leansearch_items=[
            {"name": "unknown_lemma", "module": module_path, "type": "x", "docstring": "y"}
        ],
        outline_by_path={module_path: []},  # decl not in outline
    )
    prover = _make_prover(lsp)

    event = prover._retrieve_lean_search_premises("query", k=5)

    assert len(event.retrieved_premises) == 1
    premise = event.retrieved_premises[0]
    assert premise.get("full_type_signature") in (None, "")
    payload = event.to_dict()
    assert payload.get("enriched_count") == 0


def test_leansearch_enrichment_caches_outline_per_file() -> None:
    """Outline is fetched once per file even when multiple premises share a module."""
    module_path = "Mathlib/Topology/Order/Compact.lean"
    lsp = _ScriptedLSPClient(
        leansearch_items=[
            {"name": "IsCompact.exists_isMaxOn", "module": module_path, "type": "x"},
            {"name": "IsCompact.exists_isMinOn", "module": module_path, "type": "y"},
        ],
        outline_by_path={
            module_path: [
                {"name": "IsCompact.exists_isMaxOn", "line": 246, "column": 0},
                {"name": "IsCompact.exists_isMinOn", "line": 228, "column": 0},
            ]
        },
        hover_by_position={
            (module_path, 246, 0): "full max sig",
            (module_path, 228, 0): "full min sig",
        },
    )
    prover = _make_prover(lsp)

    prover._retrieve_lean_search_premises("query", k=5)
    assert lsp.outline_calls == 1, (
        f"outline should be cached per file, got {lsp.outline_calls} calls"
    )
    assert lsp.hover_calls == 2, f"expected one hover per premise, got {lsp.hover_calls}"


# ──────────────────────────────────────────────────────────────────────────────
# Task 3 — second retrieval pass + decomposition hint + hybrid budget bump
# ──────────────────────────────────────────────────────────────────────────────


def test_should_do_second_retrieval_boundary_conditions() -> None:
    """The stall heuristic fires only on turn 1 with no progress and budget > 30%."""
    from src.observability.models import ProgressDelta
    prover = _make_prover(_ScriptedLSPClient())

    no_progress = ProgressDelta(goals_reduced=False)
    progress = ProgressDelta(goals_reduced=True)

    assert prover._should_do_second_retrieval(turn=1, last_delta=no_progress, budget_remaining_frac=0.5)
    assert not prover._should_do_second_retrieval(turn=1, last_delta=progress, budget_remaining_frac=0.5)
    assert not prover._should_do_second_retrieval(turn=2, last_delta=no_progress, budget_remaining_frac=0.5)
    assert not prover._should_do_second_retrieval(turn=1, last_delta=no_progress, budget_remaining_frac=0.2)
    assert not prover._should_do_second_retrieval(turn=1, last_delta=None, budget_remaining_frac=0.5)


def test_decomposition_hint_appears_for_quantified_goal() -> None:
    """When a goal contains ∀, ∃, ∧, or ↔, the prompt instructions include the decomposition rule."""
    import json as _json
    from src.prover.prover import ProverTarget
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t",
        claim="quantified",
        lean_code="theorem t : True := sorry",
        claim_type="mathlib_native",
    )
    target = ProverTarget(name="t", statement="True", kind="theorem_body", helper_theorem_name="t")
    state_quantified = {"goals": ["∀ x, ∃ y, P x y"], "code": "...", "diagnostics": [], "code_actions": []}
    state_plain = {"goals": ["P x"], "code": "...", "diagnostics": [], "code_actions": []}

    prompt_q = prover._build_mathlib_harness_prompt(
        packet=packet, target=target, state=state_quantified, retrieved_premises=[],
        diagnostics=None, code_actions=None, prior_trace=[],
    )
    prompt_p = prover._build_mathlib_harness_prompt(
        packet=packet, target=target, state=state_plain, retrieved_premises=[],
        diagnostics=None, code_actions=None, prior_trace=[],
    )

    body_q = _json.loads(prompt_q)
    body_p = _json.loads(prompt_p)
    rules_q = body_q["instructions"]["rules"]
    rules_p = body_p["instructions"]["rules"]
    assert any("intro" in r or "obtain" in r or "split" in r for r in rules_q), (
        f"decomposition rule missing for quantified goal: {rules_q}"
    )
    assert not any("intro" in r and "obtain" in r for r in rules_p), (
        f"decomposition rule should NOT appear for plain goal: {rules_p}"
    )


def test_hybrid_budget_is_bumped_for_mathlib_native_only() -> None:
    """Mathlib-native claims get +2 search calls and +4 prove steps; preamble_definable does not."""
    from src.config import (
        MAX_PROVE_STEPS,
        MAX_PROVE_STEPS_HYBRID,
        MAX_SEARCH_TOOL_CALLS,
        MAX_SEARCH_TOOL_CALLS_HYBRID,
    )

    assert MAX_SEARCH_TOOL_CALLS_HYBRID == MAX_SEARCH_TOOL_CALLS + 2
    assert MAX_PROVE_STEPS_HYBRID == MAX_PROVE_STEPS + 4
