"""Prompt builders for Lean 4 formalization."""

from __future__ import annotations

from src.formalizer.models import FormalizerContext
from src.skills import load_skill

FEW_SHOT_EXAMPLES = """
Few-shot examples of good Formalizer JSON:

Example 1: simple continuous-preference claim
Claim: "Show a continuous preference induces continuity on the represented utility map."
Output:
{
  "theorem_name": "formalizer_continuous_preference_continuity",
  "theorem_docstring": "Expose the continuity consequence already packaged in the preference preamble.",
  "theorem_statement": "ContinuousPreference u -> ContinuousOn u Set.univ",
  "open_statements": [],
  "subgoals": [
    {
      "name": "h_pref_continuous",
      "statement": "ContinuousPreference u -> ContinuousOn u Set.univ",
      "rationale": "Directly target `continuousPreference_continuousOn`; do not use `True`."
    }
  ],
  "final_expression": "exact h_pref_continuous"
}

Example 2: medium Bellman/fixed-point claim
Claim: "Prove the Bellman operator is monotone and connect it to the fixed-point value function."
Output:
{
  "theorem_name": "formalizer_bellman_monotone_fixed_point",
  "theorem_docstring": "Break the Bellman claim into Prover-ready monotonicity and fixed-point obligations.",
  "theorem_statement": "(∀ {S : Type*} (reward : S → ℝ) (transition : S → S) (β : ℝ), 0 ≤ β -> ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) -> ∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s) ∧ (∀ {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, IsContraction f -> ∃ x, Function.IsFixedPt f x)",
  "open_statements": [],
  "subgoals": [
    {
      "name": "h_bellman_monotone",
      "statement": "∀ {S : Type*} (reward : S → ℝ) (transition : S → S) (β : ℝ), 0 ≤ β -> ∀ {v w : S → ℝ}, (∀ s, v s ≤ w s) -> ∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s",
      "rationale": "Use `BellmanOperator.monotone`; the local proof can start with the tactic hint `simpa using add_le_add_left hmul (reward s)`."
    },
    {
      "name": "h_fixed_point",
      "statement": "∀ {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, IsContraction f -> ∃ x, Function.IsFixedPt f x",
      "rationale": "Use `contraction_has_fixedPoint`; prefer explicit imported identifiers over placeholders."
    }
  ],
  "final_expression": "exact ⟨h_bellman_monotone, h_fixed_point⟩"
}

Example 3: medium equilibrium existence claim
Claim: "Show a contraction-based equilibrium map has a fixed point."
Output:
{
  "theorem_name": "formalizer_equilibrium_fixed_point",
  "theorem_docstring": "Expose the fixed-point certificate needed by the prover.",
  "theorem_statement": "∀ {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, IsContraction f -> ∃ x, Function.IsFixedPt f x",
  "open_statements": [],
  "subgoals": [
    {
      "name": "h_contraction_fixed_point",
      "statement": "∀ {α : Type*} [MetricSpace α] [CompleteSpace α] [Nonempty α] {f : α → α}, IsContraction f -> ∃ x, Function.IsFixedPt f x",
      "rationale": "Cite `contraction_has_fixedPoint`; keep the proposition narrow and Prover-ready."
    }
  ],
  "final_expression": "exact h_contraction_fixed_point"
}
""".strip()


def build_system_prompt() -> str:
    rubric = load_skill("faithfulness_rubric") or ""
    preamble_model = load_skill("econ_preamble_model") or ""
    contribution = load_skill("econ_preamble_contribution") or ""
    return "\n".join(
        [
            "You are the Lean Econ v3 Formalizer.",
            "Generate a faithful Lean 4 sorry stub from the planner packet and structured Preamble context.",
            "Output strict JSON only.",
            "Never use `import Mathlib`.",
            "Use exact LeanEcon.Preamble module paths supplied in context.",
            "Emit one final theorem and encode intermediate planner subgoals as local `have` or `suffices` obligations.",
            "Produce Prover-ready subgoals, not placeholders.",
            "Do not emit `True`, `False`, tautologies, or generic filler subgoals unless the claim itself is trivial.",
            "Each subgoal must reference concrete imported identifiers, definitions, lemmas, or theorem templates from the selected Preamble context.",
            "When tactic hints are provided, use them in the subgoal rationale whenever they meaningfully suggest the local proof shape.",
            "Use the minimum sufficient number of named subgoals: 1 for direct closures, 2 to 4 for ordinary claims, and 5 to 6 only for genuine decomposition.",
            "Keep theorem statements faithful to the claim and Preamble concepts.",
            "",
            "Faithfulness rubric:",
            rubric,
            "",
            "Preamble operating model:",
            preamble_model,
            "",
            "Preamble contribution rules:",
            contribution,
            "",
            FEW_SHOT_EXAMPLES,
        ]
    ).strip()


def _target_subgoal_range(context: FormalizerContext) -> str:
    if len(context.preamble_entries) >= 3 or len(context.planner_subgoals) >= 4:
        return "Target 4 to 6 named subgoals for this claim."
    if len(context.planner_subgoals) <= 1:
        return "Target 1 to 2 named subgoals and mirror the authoritative stub when it is already direct."
    return "Target 1 to 4 named subgoals unless the claim clearly decomposes further."


def _identifier_inventory(context: FormalizerContext) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    for entry in context.preamble_entries:
        for group in (entry.definitions, entry.proven_lemmas, entry.related):
            for identifier in group:
                cleaned = str(identifier).strip()
                if cleaned and cleaned not in seen:
                    identifiers.append(cleaned)
                    seen.add(cleaned)
    return identifiers


def build_user_prompt(context: FormalizerContext) -> str:
    preamble_lines: list[str] = []
    for entry in context.preamble_entries:
        preamble_lines.extend(
            [
                f"- name: {entry.name}",
                f"  module: {entry.lean_module}",
                f"  description: {entry.description}",
                f"  definitions: {entry.definitions}",
                f"  definition_signatures: {entry.definition_signatures}",
                f"  proven_lemmas: {entry.proven_lemmas}",
                f"  theorem_template: {entry.theorem_template}",
                f"  tactic_hints: {entry.tactic_hints}",
                f"  textbook_source: {entry.textbook_source}",
                f"  related: {entry.related}",
                "  source_excerpt:",
                entry.source_excerpt or "  (empty)",
                "",
            ]
        )
    subgoals = "\n".join(f"- {subgoal}" for subgoal in context.planner_subgoals) or "- none"
    defaults = "\n".join(f"- {default}" for default in context.textbook_defaults) or "- none"
    identifiers = "\n".join(f"- {identifier}" for identifier in _identifier_inventory(context)) or "- none"
    return "\n".join(
        [
            "Formalize the following economic claim into Lean 4:",
            context.claim,
            "",
            _target_subgoal_range(context),
            "",
            "Planner plan paragraph:",
            context.plan_paragraph or "(none)",
            "",
            "Planner textbook defaults:",
            defaults,
            "",
            "Planner theorem-shaped subgoals:",
            subgoals,
            "",
            "Selected exact imports:",
            "\n".join(f"- {module}" for module in context.imports) or "- none",
            "",
            "High-value identifiers from the selected Preamble entries:",
            identifiers,
            "",
            "Structured Preamble context:",
            "\n".join(preamble_lines) if preamble_lines else "- none",
            "",
            "Return JSON matching this schema:",
            "{",
            '  "theorem_name": "formalizer_<slug>",',
            '  "theorem_docstring": "short economic interpretation",',
            '  "theorem_statement": "<Lean proposition>",',
            '  "open_statements": ["Classical"],',
            '  "subgoals": [{"name": "h_subgoal_1", "statement": "<Lean proposition>", "rationale": "..." }],',
            '  "final_expression": "optional final exact expression such as `exact ⟨h_subgoal_1, h_subgoal_2⟩` or null"',
            "}",
            "",
            "The theorem must parse in Lean when wrapped with the provided imports and local sorry placeholders.",
            "The subgoals must be claim-specific and use actual Preamble concepts rather than `True`.",
        ]
    )


def build_revision_user_prompt(
    context: FormalizerContext,
    *,
    previous_score: float,
    feedback: list[str],
    prior_lean_code: str,
) -> str:
    return "\n".join(
        [
            "Revision request: the previous formalization was not faithful enough.",
            f"Previous faithfulness score: {previous_score:.3f} (must be at least 4.5 for auto-pass).",
            "Feedback:",
            *[f"- {item}" for item in feedback],
            "",
            "Revise conservatively:",
            "- Make the theorem statement narrower and closer to imported Preamble lemmas.",
            "- Replace any generic or tautological subgoals with explicit propositions mentioning imported identifiers.",
            "- Prefer the minimum sufficient number of subgoals; do not pad the packet to hit an arbitrary count.",
            "- Use tactic hints in the rationale when they suggest the local proof route.",
            "",
            "Previous Lean stub:",
            prior_lean_code,
            "",
            "Original request and context:",
            build_user_prompt(context),
        ]
    )
