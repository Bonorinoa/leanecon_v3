"""Prompt builders for the HILBERT planner."""

from __future__ import annotations

from src.planner.models import PlannerContext
from src.skills import load_skill


def build_system_prompt() -> str:
    """Compose the authoritative system prompt from runtime skill documents."""

    hilbert = load_skill("hilbert_protocol") or ""
    preamble_model = load_skill("econ_preamble_model") or ""
    preamble_contribution = load_skill("econ_preamble_contribution") or ""
    return "\n\n".join(
        [
            "You are the Lean Econ v3 Planner, a HILBERT-style informal reasoner for economic theory.",
            "Follow the repository's authoritative planner and preamble specifications exactly.",
            "Your job is to prepare a faithful, economist-facing plan for later Lean formalization.",
            "Non-negotiable rules:",
            "- Emit strict JSON only. No Markdown fences and no extra commentary.",
            "- ALWAYS emit every required key even if empty list.",
            "- Ask at most 3 clarifying questions, and only when ambiguity materially affects formalization.",
            "- Always emit textbook defaults and exactly 1 concise paragraph plan with LaTeX math.",
            "- Use the minimum sufficient number of subgoals. Emit 1 subgoal when the theorem stub or retrieved preamble theorem is directly closable, 2-4 for ordinary claims, and 5-6 only when the argument genuinely decomposes.",
            "- Subgoals must be specific, theorem-shaped Lean statements that reference actual LeanEcon Preamble concepts when available. Avoid generic ': True' placeholders.",
            "- Emit a confidence score in [0.0, 1.0] for the overall plan.",
            "- Use retrieved Preamble concepts and verified memory traces as grounding; do not invent unsupported lemmas.",
            "- If clarifying questions remain, still emit a full packet and set needs_review to true.",
            "Authoritative specs:",
            hilbert,
            preamble_model,
            preamble_contribution,
        ]
    ).strip()


def build_user_prompt(
    claim: str,
    context: PlannerContext,
    *,
    theorem_stub: str | None = None,
    preamble_names: list[str] | None = None,
) -> str:
    """Build the user prompt with retrieval context and the runtime contract."""

    selected_preamble = context.preamble_context or "No relevant Preamble context retrieved."
    memory_context = context.memory_context or "No verified memory traces retrieved."

    sections: list[str] = [f"Claim: {claim}", ""]

    if theorem_stub:
        sections.extend(
            [
                "Authoritative Lean 4 theorem stub (treat as ground truth — plan toward closing THIS exact goal):",
                "```lean",
                theorem_stub.strip(),
                "```",
                "",
            ]
        )
    if preamble_names:
        sections.extend(
            [
                "Named preamble entries relevant to this claim:",
                ", ".join(preamble_names),
                "",
            ]
        )

    sections.extend(
        [
            "Retrieved Preamble context:",
            selected_preamble,
            "",
            "Verified few-shot traces:",
            memory_context,
            "",
            "Output contract:",
            "- `plan_paragraph` must be exactly one concise paragraph and must contain LaTeX math.",
            "- `subgoals`: emit the minimum sufficient number of subgoals needed to close the stub. Use 1 for direct closures, 2-4 for ordinary claims, and only use 5-6 when the proof genuinely decomposes.",
            "- Never fabricate subgoals the stub does not imply. Never rewrite the theorem signature — it is authoritative.",
            "- `subgoals` should mention retrieved Preamble concepts such as BellmanOperator, IsContraction, Function.IsFixedPt, ValueFunction, or other relevant LeanEcon symbols when supported by context.",
            "- `needs_review` should be true whenever ambiguity remains or human approval is still required.",
            "- `confidence` must be a float between 0.0 and 1.0.",
            "- Reuse the 1-2 few-shot examples above when they are structurally relevant.",
        ]
    )
    return "\n".join(sections)
