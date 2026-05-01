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
    """Stage 2-followup C: the stall heuristic fires whenever the previous
    turn made no progress and budget > 30%, regardless of turn index. Per-target
    idempotence is enforced at the call site, not in this pure helper.
    """
    from src.observability.models import ProgressDelta
    prover = _make_prover(_ScriptedLSPClient())

    no_progress = ProgressDelta(goals_reduced=False)
    progress = ProgressDelta(goals_reduced=True)

    assert prover._should_do_second_retrieval(last_delta=no_progress, budget_remaining_frac=0.5)
    assert prover._should_do_second_retrieval(last_delta=no_progress, budget_remaining_frac=0.5, turn=2)
    assert prover._should_do_second_retrieval(last_delta=no_progress, budget_remaining_frac=0.5, turn=4)
    assert not prover._should_do_second_retrieval(last_delta=progress, budget_remaining_frac=0.5)
    assert not prover._should_do_second_retrieval(last_delta=no_progress, budget_remaining_frac=0.2)
    assert not prover._should_do_second_retrieval(last_delta=None, budget_remaining_frac=0.5)


def test_decomposition_hint_appears_for_quantified_goal() -> None:
    """Updated (Stage 1 Task 2): strengthened hint with specific tactics + multi-step
    pattern examples now appears for quantified goals (per lean4_proving skill)."""
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
    assert any(
        "intro" in r
        or "obtain" in r
        or "refine" in r
        or "constructor" in r
        or "Consider patterns" in r
        for r in rules_q
    ), f"strengthened hint+examples missing for quantified goal: {rules_q}"
    assert not any("intro" in r and "obtain" in r for r in rules_p), (
        f"decomposition rule should NOT appear for plain goal: {rules_p}"
    )


def test_harness_prompt_includes_strengthened_hint_examples_and_refined_query() -> None:
    """Stage 1 Task 2 deliverable test: verifies updated actionable hint,
    3 multi-step patterns ('Consider patterns'), and targeted second-pass query
    (includes key goal terms + 'theorem'). Preserves behavior on other claims."""
    import json as _json
    from src.prover.prover import ProverTarget
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t2_extreme_value_repair",
        claim="quantified extreme-value style claim",
        lean_code="theorem t2 : ∃ x, P x := sorry",
        claim_type="mathlib_native",
    )
    target = ProverTarget(
        name="t2", statement="∃ x, P x", kind="theorem_body", helper_theorem_name="t2"
    )
    state = {
        "goals": ["∃ x ∈ s, IsMaxOn f s x ∧ ContinuousOn f s"],
        "code": "...",
        "diagnostics": [],
        "code_actions": [],
    }

    prompt = prover._build_mathlib_harness_prompt(
        packet=packet,
        target=target,
        state=state,
        retrieved_premises=[],
        diagnostics=None,
        code_actions=None,
        prior_trace=[],
    )
    body = _json.loads(prompt)
    rules = body["instructions"]["rules"]
    assert any("refine" in r and "constructor" in r for r in rules), (
        f"expected strengthened hint: {rules}"
    )
    assert any("Consider patterns" in r for r in rules), "multi-step examples missing"
    assert any("intro h" in r or "obtain" in r for r in rules), "patterns incomplete"

    # Verify improved second-pass query (targeted)
    refined = prover._refined_leansearch_query(state)
    assert refined is not None
    assert "theorem" in refined.lower()
    assert "IsMaxOn" in refined or "ContinuousOn" in refined, f"key terms missing: {refined}"


def test_hybrid_budget_is_bumped_for_mathlib_native_only() -> None:
    """Mathlib-native claims get synthesis recovery budget; preamble_definable does not."""
    from src.config import (
        MAX_PROVE_STEPS,
        MAX_PROVE_STEPS_HYBRID,
        MAX_SEARCH_TOOL_CALLS,
        MAX_SEARCH_TOOL_CALLS_HYBRID,
    )

    assert MAX_SEARCH_TOOL_CALLS_HYBRID == MAX_SEARCH_TOOL_CALLS + 4
    assert MAX_PROVE_STEPS_HYBRID == MAX_PROVE_STEPS + 8


def test_leansearch_failure_is_observable_and_retries_with_refined_query() -> None:
    """Stage 1 Task 1: lean_leansearch failures (0 results or exception) now emit
    structured LeanSearchFailureEvent (visible in benchmark JSONL) and retry once
    using refined subgoal text from state. Success paths, budget, and second-pass
    logic are fully preserved.
    """
    from unittest.mock import patch

    # Mock that always returns empty (triggers retry + final failure event for test coverage)
    class _FailingLSP(_ScriptedLSPClient):
        def __init__(self) -> None:
            super().__init__(leansearch_items=[])
            self.call_count = 0

        def lean_leansearch(self, query: str, *, num_results: int = 8):
            self.call_count += 1
            return {"items": []}  # consistent empty to trigger observable failure + retry

    lsp = _FailingLSP()
    prover = _make_prover(lsp)
    state = {"goals": ["current_unsolved_subgoal : ∀ x, P x"]}

    with patch("src.prover.prover.log_event") as mock_log:  # patch where imported in prover.py
        event = prover._retrieve_lean_search_premises(
            "original claim with no direct hits", k=3, state=state
        )

        assert len(event.retrieved_premises) == 0
        assert "current_unsolved_subgoal" in str(event.query) or "refined" in str(event.query).lower()
        assert lsp.call_count == 2, "exactly one retry occurred on failure"

        # Verify structured LeanSearchFailureEvent emitted (with retry flag)
        failure_calls = [
            c for c in mock_log.call_args_list if c and "LeanSearchFailureEvent" in str(c)
        ]
        assert len(failure_calls) >= 1, "LeanSearchFailureEvent must be logged for observability"
        assert any("retry_attempted" in str(c) and "True" in str(c) for c in failure_calls)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — deeper synthesis (P1.A/B/C) + hygiene (H.1/H.2)
# ──────────────────────────────────────────────────────────────────────────────


def test_refined_query_extracts_mathlib_idents_from_full_goal_text() -> None:
    """Stage 2 P1.A: refined query is built from CamelCase Mathlib identifiers
    appearing anywhere in the goal/hypothesis text (not just after ⊢).
    """
    prover = _make_prover(_ScriptedLSPClient())
    state = {
        "goals": [
            "f : ℕ → ℝ\nhmono : Monotone f\nhbdd : BddAbove (Set.range f)\n⊢ ∃ L, Tendsto f atTop (𝓝 L)"
        ]
    }
    refined = prover._refined_leansearch_query(state)
    assert refined is not None
    text = refined.lower()
    assert "monotone" in text or "bddabove" in text or "tendsto" in text, (
        f"hypothesis-derived idents missing from refined query: {refined}"
    )
    assert "theorem" in text


def test_refined_query_falls_back_when_no_mathlib_idents() -> None:
    """Stage 2 P1.A: non-Mathlib goal still gets the original goal-line + theorem fallback."""
    prover = _make_prover(_ScriptedLSPClient())
    state = {"goals": ["⊢ p ∨ q"]}
    refined = prover._refined_leansearch_query(state)
    assert refined is not None
    assert "theorem" in refined.lower()


def test_harness_prompt_includes_premise_utilization_section() -> None:
    """Stage 2 P1.B: every mathlib_native prompt carries the premise-utilization
    protocol that teaches the model to consume enriched premise fields.
    """
    import json as _json
    from src.prover.prover import ProverTarget
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t",
        claim="any",
        lean_code="theorem t : True := sorry",
        claim_type="mathlib_native",
    )
    target = ProverTarget(name="t", statement="True", kind="theorem_body", helper_theorem_name="t")
    state = {"goals": ["P x"], "code": "...", "diagnostics": [], "code_actions": []}

    prompt = prover._build_mathlib_harness_prompt(
        packet=packet, target=target, state=state, retrieved_premises=[],
        diagnostics=None, code_actions=None, prior_trace=[],
    )
    body = _json.loads(prompt)
    util = body["instructions"].get("premise_utilization")
    assert isinstance(util, list) and len(util) >= 3, (
        f"premise_utilization section missing or too short: {util}"
    )
    joined = "\n".join(util)
    for token in ("full_type_signature", "detailed_docstring", "declaration_location"):
        assert token in joined, f"{token} guidance missing from premise_utilization"


def test_sprint25_harness_prompt_includes_proof_sketch_and_few_shots() -> None:
    import json as _json
    from src.prover.prover import ProverTarget
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t",
        claim="monotone bounded convergence",
        lean_code="theorem t : True := sorry",
        claim_type="mathlib_native",
    )
    target = ProverTarget(name="t", statement="True", kind="theorem_body", helper_theorem_name="t")
    prompt = prover._build_mathlib_harness_prompt(
        packet=packet,
        target=target,
        state={"goals": ["⊢ ∃ l, Tendsto u atTop (𝓝 l)"], "code": "..."},
        retrieved_premises=[
            {
                "name": "tendsto_atTop_ciSup",
                "type": "Monotone u → BddAbove (Set.range u) → Tendsto u atTop (𝓝 _)",
            }
        ],
        diagnostics=None,
        code_actions=None,
        prior_trace=[],
        proof_sketch={
            "strategy": "Use the Tendsto premise, then wrap the limit witness.",
            "likely_premises": ["tendsto_atTop_ciSup"],
            "subgoal_order": ["prove Tendsto"],
            "tactic_shape": "exact ⟨_, tendsto_atTop_ciSup hmono hbdd⟩",
            "source": "unit_test",
        },
    )

    body = _json.loads(prompt)
    assert body["proof_sketch"]["likely_premises"] == ["tendsto_atTop_ciSup"]
    assert len(body["synthesis_few_shots"]) == 3
    assert any("compact" in shot["name"] for shot in body["synthesis_few_shots"])


def test_sprint25_decomposition_hint_triggers_on_compact_and_convergence_markers() -> None:
    prover = _make_prover(_ScriptedLSPClient())

    assert prover._goals_need_decomposition_hint(
        ["hcompact : IsCompact s\nhf : ContinuousOn f s\n⊢ ∃ x, IsMaxOn f s x"]
    )
    assert prover._goals_need_decomposition_hint(
        ["hmono : Monotone u\nhbdd : BddAbove (Set.range u)\n⊢ Tendsto u atTop (𝓝 l)"]
    )
    assert not prover._goals_need_decomposition_hint(["⊢ P x"])


def test_sprint25_synthesis_event_payload_tracks_premise_match() -> None:
    from src.observability.models import SynthesisEvent
    from src.prover.synthesizer import ProofSynthesizer

    match = ProofSynthesizer().premise_match(
        "exact Real.tendsto_of_bddAbove_monotone hbdd hmono",
        [
            {"name": "Real.tendsto_of_bddAbove_monotone"},
            {"name": "tendsto_atTop_ciSup"},
        ],
    )
    payload = SynthesisEvent(
        tactic="exact Real.tendsto_of_bddAbove_monotone hbdd hmono",
        referenced_premises=match.referenced_premises,
        top3_match=match.top3_match,
        success=True,
        target_name="h",
        claim_id="c",
        decomposition_depth=1,
    ).to_dict()

    assert payload["event_type"] == "SynthesisEvent"
    assert payload["referenced_premises"] == ["Real.tendsto_of_bddAbove_monotone"]
    assert payload["top3_match"] is True
    assert payload["decomposition_depth"] == 1


def test_sprint25_helper_lemma_memory_round_trips(tmp_path) -> None:
    from src.memory.store import ProofTraceStore
    from src.prover.memory_writer import ProverMemoryWriter
    from tests.test_prover import _packet

    store = ProofTraceStore(tmp_path / "proof_traces.sqlite3")
    packet = _packet(
        theorem_name="parent",
        claim="monotone bounded convergence",
        lean_code="theorem parent : True := sorry",
        claim_type="mathlib_native",
    )
    writer = ProverMemoryWriter(store)
    writer.record_helper_lemma(
        packet=packet,
        lemma_name="apollo_parent_synth_1",
        lemma_statement="Tendsto u atTop (𝓝 l)",
        tactic_sequence=["exact tendsto_atTop_ciSup hmono hbdd"],
        parent_claim_id="parent",
        retrieved_premises=[{"name": "tendsto_atTop_ciSup"}],
        prover_backend="leanstral",
    )

    traces = store.query_mathlib_helpers(["Tendsto", "ciSup"], limit=2)
    assert len(traces) == 1
    assert traces[0].claim_id == "apollo_parent_synth_1"
    assert traces[0].trace_metadata["memory_kind"] == "mathlib_helper_lemma"


def test_sprint25_best_of_n_is_disabled_by_default() -> None:
    from src.config import MATHLIB_SYNTHESIS_BEST_OF_N

    assert MATHLIB_SYNTHESIS_BEST_OF_N == 1


def test_harness_prompt_includes_exists_membership_pattern() -> None:
    """Stage 2 P1.C: 4th multi-step pattern (∃ x ∈ s, P x ∧ Q x) is present
    when the goal is quantified.
    """
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
    state = {
        "goals": ["∃ x ∈ s, IsMaxOn f s x ∧ ContinuousOn f s"],
        "code": "...",
        "diagnostics": [],
        "code_actions": [],
    }
    prompt = prover._build_mathlib_harness_prompt(
        packet=packet, target=target, state=state, retrieved_premises=[],
        diagnostics=None, code_actions=None, prior_trace=[],
    )
    rules = _json.loads(prompt)["instructions"]["rules"]
    assert any("ExistsMembership-Conjunctive" in r for r in rules), (
        f"4th pattern missing from rules: {rules}"
    )


def test_mathlib_rag_failure_emits_audit_event_and_error_code() -> None:
    """Stage 2 H.2: when the underlying RAG raises, _retrieve_mathlib_premises
    emits an lsp_tool_error audit and stamps RetrievalEvent.error_code.
    """
    from unittest.mock import patch

    prover = _make_prover(_ScriptedLSPClient())

    def _boom(*args, **kwargs):
        raise RuntimeError("rag exploded")

    with patch("src.retrieval.mathlib_rag.retrieve_premises", side_effect=_boom), \
         patch.object(prover, "_handle_lsp_error") as handle:
        event = prover._retrieve_mathlib_premises(["⊢ True"], k=3, claim_id="c1")

    assert event.retrieved_premises == []
    assert event.error_code == "mathlib_rag_unavailable"
    assert event.claim_id == "c1"
    assert handle.called, "expected _handle_lsp_error to be invoked on RAG failure"
    args = handle.call_args.args
    assert args[0] == "mathlib_rag"


def test_retrieval_events_carry_claim_id_through_to_dict() -> None:
    """Stage 2 H.1: claim_id round-trips through both event types' to_dict()."""
    from src.observability.models import LeanSearchFailureEvent, RetrievalEvent

    rev = RetrievalEvent(claim_id="c-42", error_code=None)
    assert rev.to_dict()["claim_id"] == "c-42"
    assert rev.to_dict()["error_code"] is None

    fev = LeanSearchFailureEvent(claim_id="c-42")
    assert fev.to_dict()["claim_id"] == "c-42"


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2-followup — trace-grounded fixes (A/B/C/D)
# ──────────────────────────────────────────────────────────────────────────────


def test_first_pass_search_query_uses_camel_case_idents_when_available() -> None:
    """Followup B: the first-pass LeanSearch query prefers Mathlib CamelCase
    identifiers when the goal/theorem text contains them, replacing the prior
    verbose-claim-text fallback that returned 0 hits in the benchmark trace.
    """
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t2_monotone_sequence_converges",
        claim="A monotone sequence bounded above converges.",
        lean_code=(
            "theorem t : ∀ {u : ℕ → ℝ}, Monotone u → BddAbove (Set.range u)"
            " → ∃ l, Tendsto u atTop (𝓝 l) := sorry"
        ),
        claim_type="mathlib_native",
    )
    goals = [
        "u : ℕ → ℝ\nhmono : Monotone u\nhbdd : BddAbove (Set.range u)"
        "\n⊢ ∃ l, Tendsto u atTop (𝓝 l)"
    ]
    query = prover._mathlib_native_search_query(
        packet=packet,
        goals=goals,
        current_code=(
            "theorem t : ∀ {u : ℕ → ℝ}, Monotone u → BddAbove (Set.range u)"
            " → ∃ l, Tendsto u atTop (𝓝 l) := sorry"
        ),
    )
    text = query.lower()
    assert "monotone" in text or "bddabove" in text or "tendsto" in text, (
        f"first-pass query missing Mathlib idents: {query}"
    )
    assert "theorem" in text, query
    assert "bounded above converges" not in text, (
        f"verbose claim phrase leaked into refined query: {query}"
    )


def test_first_pass_search_query_falls_back_when_no_idents() -> None:
    """Followup B: with no Mathlib idents in scope we keep the original
    verbose-claim behaviour to preserve hits on natural-language claims.
    """
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t",
        claim="Some plain claim.",
        lean_code="theorem t : True := sorry",
        claim_type="mathlib_native",
    )
    query = prover._mathlib_native_search_query(
        packet=packet,
        goals=["⊢ True"],
        current_code="theorem t : True := sorry",
    )
    assert "Some plain claim" in query


def test_harness_defers_to_lsp_fallback_on_empty_goals() -> None:
    """Followup A (corrected): when the harness probes an empty goal state, it
    must NOT run the model AND must NOT claim closure. Instead it returns
    (None, None) so the outer loop yields to the LSP-search fallback — which
    is the path that previously closed claims like
    t2_contraction_mapping_fixed_point.

    Trace evidence: the first attempt at this fix returned ``(code, None)``
    which signalled spurious target closure and broke the final compile check
    (regressed tier2 mathlib_native to 0/3 with ``final_compile_failed``).
    """
    from src.prover.prover import ProverTarget
    from tests.test_prover import _packet

    prover = _make_prover(_ScriptedLSPClient())
    packet = _packet(
        theorem_name="t2_extreme_value_repair",
        claim="anything",
        lean_code="theorem t : True := by trivial",
        claim_type="mathlib_native",
    )
    target = ProverTarget(
        name="h_exists_maximum",
        statement="True",
        kind="subgoal",
        helper_theorem_name="h_exists_maximum",
    )

    class _StubSession:
        proof_path = None
        solved = False

        def __init__(self) -> None:
            self._code = "theorem t : True := by trivial"

        def read_code(self) -> str:
            return self._code

        def write_code(self, value: str) -> None:
            self._code = value

        def get_goals(self) -> list[str]:
            return []

    session = _StubSession()
    backend = prover.primary_backend
    audit_events: list = []

    import asyncio
    from src.prover.models import ProverTargetTimeouts

    closed, failure = asyncio.run(
        prover._try_mathlib_native_harness_loop(
            packet=packet,
            target=target,
            session=session,
            trace=[],
            audit_events=audit_events,
            backend=backend,
            attempted_backends=[],
            turn=2,
            timeout=30,
            telemetry=type("T", (), {"record_provider": staticmethod(lambda *a, **k: None)})(),
            provider_usage=[],
            lean_feedback=[],
            goals=[],
            job_id="job_test",
            on_progress=None,
            target_timeouts=ProverTargetTimeouts(
                theorem_body=30,
                subgoal=30,
                apollo_lemma=30,
            ),
            max_turns=2,
            allow_decomposition=True,
            max_recursion_depth=1,
        )
    )

    # Critical: must NOT claim closure (would skip downstream LSP fallback +
    # final compile check). Must NOT report failure either — defer cleanly.
    assert closed is None
    assert failure is None
    # Audit event records the deferral so post-mortems can see it.
    event_types = [getattr(e, "event_type", None) for e in audit_events]
    assert "mathlib_native_harness_skipped_empty_goals" in event_types, event_types


def test_extract_unknown_identifier_pulls_name_from_lean_error() -> None:
    """Followup D helper: the regex must lift the failed identifier out of the
    common Lean error spellings. Trace evidence: t2_monotone_sequence_converges
    line 49 — ``Unknown identifier `monotone_bddAbove_converges```.
    """
    from src.prover.prover import (
        _extract_unknown_identifier,
        _query_from_failed_identifier,
    )

    backtick_form = "Unknown identifier `monotone_bddAbove_converges`"
    quote_form = "unknown identifier 'foo_bar_baz'"
    not_an_error = "All goals solved."

    assert _extract_unknown_identifier(backtick_form) == "monotone_bddAbove_converges"
    assert _extract_unknown_identifier(quote_form) == "foo_bar_baz"
    assert _extract_unknown_identifier(not_an_error) is None

    query = _query_from_failed_identifier("monotone_bddAbove_converges")
    text = query.lower()
    assert "monotone" in text and "bddabove" in text and "converges" in text
    assert "theorem" in text


def test_rescue_query_from_recent_trace_finds_unknown_identifier() -> None:
    """Followup D end-to-end: a prior harness step that errored with
    ``unknown identifier`` produces a rescue query for the current target.
    """
    from src.prover.prover import Prover
    from src.prover.models import ProverTraceStep

    step = ProverTraceStep(
        turn=2,
        backend="leanstral",
        target_name="h_monotone_boundedAbove_converges",
        action_type="mathlib_native_harness_loop",
        success=False,
        tool_name="apply_tactic",
        tool_result="Unknown identifier `monotone_bddAbove_converges`",
    )
    rescue = Prover._rescue_query_from_recent_trace(
        [step], target_name="h_monotone_boundedAbove_converges"
    )
    assert rescue is not None
    text = rescue.lower()
    assert "monotone" in text and "bddabove" in text
    assert "theorem" in text

    # No recent error → no rescue.
    clean_step = ProverTraceStep(
        turn=2,
        backend="leanstral",
        target_name="h_monotone_boundedAbove_converges",
        action_type="mathlib_native_harness_loop",
        success=True,
        tool_name="apply_tactic",
        tool_result="All goals solved.",
    )
    assert (
        Prover._rescue_query_from_recent_trace(
            [clean_step], target_name="h_monotone_boundedAbove_converges"
        )
        is None
    )
