"""Prompt builders for Lean 4 formalization."""

from __future__ import annotations

from src.formalizer.models import FormalizerContext
from src.skills import load_skill


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
            "Emit one final theorem and encode intermediate planner subgoals as local `have` or `suffices` placeholders.",
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
        ]
    ).strip()


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
    return "\n".join(
        [
            "Formalize the following economic claim into Lean 4:",
            context.claim,
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
            '  "final_expression": "optional final exact expression or null"',
            "}",
            "",
            "The theorem must parse in Lean when wrapped with the provided imports and local sorry placeholders.",
        ]
    )
