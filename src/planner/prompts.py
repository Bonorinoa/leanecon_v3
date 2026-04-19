"""Prompt builders for the HILBERT planner."""

from __future__ import annotations

import json

from src.planner.models import PlannerContext, PlannerLLMResponse
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
            "- Ask at most 3 clarifying questions, and only when ambiguity materially affects formalization.",
            "- Always emit textbook defaults, exactly 1 concise paragraph plan with LaTeX math, and 3 to 6 Lean 4 sorry subgoals.",
            "- Prefer 4 to 6 subgoals when the claim naturally decomposes into operator setup, assumptions, core inequality, and conclusion steps.",
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


def build_user_prompt(claim: str, context: PlannerContext) -> str:
    """Build the user prompt with retrieval context and the JSON schema contract."""

    schema = PlannerLLMResponse.model_json_schema()
    schema_json = json.dumps(schema, indent=2, sort_keys=True)
    selected_preamble = context.preamble_context or "No relevant Preamble context retrieved."
    memory_context = context.memory_context or "No verified memory traces retrieved."

    return "\n".join(
        [
            f"Claim: {claim}",
            "",
            "Retrieved Preamble context:",
            selected_preamble,
            "",
            "Verified few-shot traces:",
            memory_context,
            "",
            "Output contract:",
            "- The response must be valid JSON matching the schema below.",
            "- `plan_paragraph` must be exactly one concise paragraph and must contain LaTeX math.",
            "- `subgoals` must contain 3 to 6 Lean 4 theorem sorry statements.",
            "- `subgoals` should mention retrieved Preamble concepts such as BellmanOperator, IsContraction, Function.IsFixedPt, ValueFunction, or other relevant LeanEcon symbols when supported by context.",
            "- `needs_review` should be true whenever ambiguity remains or human approval is still required.",
            "- `confidence` must be a float between 0.0 and 1.0.",
            "- Reuse the 1-2 few-shot examples above when they are structurally relevant.",
            "",
            "JSON schema:",
            schema_json,
        ]
    )
