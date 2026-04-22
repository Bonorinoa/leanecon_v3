"""Prompt builders for the HILBERT planner."""

from __future__ import annotations

from src.planner.models import PlannerContext


def build_system_prompt() -> str:
    """Compose a compact system prompt for structured planner output."""

    return "\n".join(
        [
            "You are the Lean Econ v3 Planner.",
            "Return strict JSON only. No Markdown fences. No prose outside JSON.",
            "Use the theorem stub and selected preamble entries as ground truth.",
            "Do not rewrite the theorem signature when a stub is provided.",
            "Emit all required keys.",
            "Ask at most 3 clarifying questions, only when ambiguity blocks faithful formalization.",
            "Keep `plan_paragraph` to one concise paragraph.",
            "Use the minimum sufficient number of subgoals: 1 for direct closures, 2-3 only when the proof genuinely decomposes.",
            "Prefer direct, stub-aligned, Lean-facing subgoals over broad decompositions.",
            "Reuse retrieved preamble lemmas and tactic hints when they clearly match the claim.",
            "Do not invent unsupported lemmas, generic templates, or filler subgoals.",
        ]
    ).strip()


def build_user_prompt(
    claim: str,
    context: PlannerContext,
    *,
    theorem_stub: str | None = None,
    preamble_names: list[str] | None = None,
    benchmark_mode: bool = False,
) -> str:
    """Build the user prompt with retrieval context and the runtime contract."""

    sections: list[str] = [f"Claim: {claim}", ""]

    if theorem_stub:
        sections.extend(
            [
                "Authoritative Lean 4 theorem stub:",
                theorem_stub.strip(),
                "",
            ]
        )
    selected_names = preamble_names or [hit.name for hit in context.selected_preamble]
    if selected_names:
        sections.extend(
            [
                "Pinned preamble entries:",
                ", ".join(selected_names),
                "",
            ]
        )

    if context.preamble_context:
        sections.extend(["Selected preamble hints:", context.preamble_context, ""])
    if context.memory_context:
        sections.extend(["Relevant verified trace:", context.memory_context, ""])

    sections.extend(
        [
            "Output contract:",
            "- `clarifying_questions`: list of at most 3 strings ending in `?`; use `[]` when not needed.",
            "- `textbook_defaults`: short list of assumptions or conventions needed for formalization.",
            "- `plan_paragraph`: one concise paragraph.",
            "- `subgoals`: 1 direct stub-aligned subgoal when possible; otherwise 2-3 specific Lean-facing subgoals.",
            "- `needs_review`: true only when ambiguity remains.",
            "- `confidence`: float in [0.0, 1.0].",
        ]
    )
    if benchmark_mode and theorem_stub:
        sections.extend(
            [
                "",
                "Benchmark mode constraints:",
                "- Treat the theorem stub as authoritative and keep the theorem signature unchanged.",
                "- Emit theorem-shaped Lean subgoals only; do not emit prose bullets, tactic-only lines, or generic placeholders.",
                "- If the safest plan is direct closure, restate the exact stub as the subgoal rather than inventing helper theorem names.",
            ]
        )
    return "\n".join(sections)
