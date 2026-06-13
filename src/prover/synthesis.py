"""Prompt and hint construction for Prover.

This module owns JSON prompt payloads, memory examples, premise projection, and
metadata tactic hints. It intentionally does not execute Lean tools.
"""

from __future__ import annotations

import json
from typing import Any

from src.formalizer.models import FormalizationPacket
from src.prover.budget import MATHLIB_NATIVE_PROMPT_ONLY_TOOLS
from src.prover.models import ProverTarget, ProverTraceStep
from src.prover.synthesizer import MATHLIB_SYNTHESIS_FEW_SHOTS
from src.prover.tactics import suggest_fast_path_tactics


def _state_config_value(state_config: Any, name: str, default: Any = None) -> Any:
    if state_config is None:
        return default
    return getattr(state_config, name, default)


def _state_name_value(current_state: Any) -> str | None:
    if current_state is None:
        return None
    return str(getattr(current_state, "value", current_state))


def _is_default_synthesizing_state(
    *,
    current_state: Any,
    state_config: Any,
) -> bool:
    prompt_rules = _state_config_value(state_config, "prompt_rules", {}) or {}
    return (
        _state_name_value(current_state) in {None, "Synthesizing"}
        and _state_config_value(state_config, "memory_filter", "broad") == "broad"
        and prompt_rules.get("mode") in {None, "synthesize"}
    )


def _state_prompt_context(
    *,
    current_state: Any,
    state_config: Any,
) -> dict[str, Any] | None:
    if state_config is None or _is_default_synthesizing_state(
        current_state=current_state,
        state_config=state_config,
    ):
        return None
    return {
        "current_state": _state_name_value(current_state),
        "prompt_rules": dict(_state_config_value(state_config, "prompt_rules", {}) or {}),
        "memory_filter": _state_config_value(state_config, "memory_filter", "none"),
        "max_tool_calls": _state_config_value(state_config, "max_tool_calls"),
        "allow_decompose": _state_config_value(state_config, "allow_decompose", False),
    }


def _build_prompt(
    *,
    packet: FormalizationPacket,
    target: ProverTarget,
    current_code: str,
    tool_specs: list[dict[str, Any]],
    lean_feedback: list[str],
    goals: list[str],
    prior_trace: list[ProverTraceStep],
    examples: list[dict[str, Any]],
    turn_hints: list[str] | None = None,
    current_state: Any = None,
    state_config: Any = None,
) -> str:
    preferred_tactics = list(
        dict.fromkeys([*(turn_hints or []), *suggest_fast_path_tactics(current_code)])
    )
    recent_steps = [
        {
            "turn": step.turn,
            "backend": step.backend,
            "action_type": step.action_type,
            "tool_name": step.tool_name,
            "success": step.success,
            "tool_result": step.tool_result,
            "lean_feedback": step.lean_feedback,
        }
        for step in prior_trace[-3:]
    ]
    state_context = _state_prompt_context(
        current_state=current_state,
        state_config=state_config,
    )
    instructions = {
        "return_json_only": True,
        "action_type": ["tool", "decompose", "finish"],
        "preferred_tactics": preferred_tactics,
        "rules": [
            "All Lean actions must go through a registered tool.",
            "Prefer apply_tactic before rewriting full code.",
            "Do not repeat the same failed tool call twice.",
            "Use decomposition only when the target is stalled.",
        ],
    }
    if state_context is not None:
        instructions["state_prompt_rules"] = state_context["prompt_rules"]
        instructions["state_memory_filter"] = state_context["memory_filter"]
        instructions["state_guidance"] = state_context["prompt_rules"].get("guidance")
    prompt_payload = {
        "claim": packet.claim,
        "theorem_name": packet.theorem_name,
        "claim_type": getattr(packet, "claim_type", None),
        "selected_preamble": packet.selected_preamble,
        "target": target.model_dump(mode="json"),
        "current_code": current_code,
        "lean_feedback": lean_feedback,
        "goals": goals,
        "memory_examples": examples,
        "synthesis_few_shots": [dict(item) for item in MATHLIB_SYNTHESIS_FEW_SHOTS],
        "tools": tool_specs,
        "recent_trace": recent_steps,
        "instructions": instructions,
        "response_schema": {
            "action_type": "tool|decompose|finish",
            "rationale": "string",
            "tool": {"name": "registered tool", "arguments": {}},
            "decomposition_statement": "Lean proposition when action_type=decompose",
            "decomposition_name": "optional theorem name",
            "finish_reason": "string when action_type=finish",
        },
    }
    if state_context is not None:
        prompt_payload["state_context"] = state_context
    return json.dumps(prompt_payload, ensure_ascii=True, indent=2)


class ProverSynthesisMixin:
    """Mixin extracted from the legacy Prover monolith."""

    def _selected_preamble_entries(self, packet: FormalizationPacket) -> list[Any]:
        from src.preamble_library import PREAMBLE_LIBRARY

        entries: list[Any] = []
        for name in packet.selected_preamble:
            entry = PREAMBLE_LIBRARY.get(name)
            if entry is not None:
                entries.append(entry)
        return entries

    def _memory_examples(self, packet: FormalizationPacket) -> list[dict[str, Any]]:
        memory_filter = self._state_memory_filter()
        if memory_filter == "none":
            examples = []
        elif memory_filter == "failure_focused":
            examples = self._failure_focused_memory_examples(packet)
        elif memory_filter == "subgoal_focused":
            examples = self._subgoal_focused_memory_examples(packet)
        elif memory_filter == "rescue_identifier":
            examples = self._rescue_identifier_memory_examples(packet)
        else:
            examples = self._broad_memory_examples(packet)
        return [
            {
                "claim_text": trace.claim_text,
                "preamble_names": trace.preamble_names,
                "tactic_sequence": trace.tactic_sequence[:4],
                "lesson_summary": trace.lesson_summary,
                "outcome": trace.outcome,
                "trace_metadata": trace.trace_metadata or {},
            }
            for trace in examples
        ]

    def _state_memory_filter(self) -> str:
        state_config = getattr(self, "current_state_config", None)
        return str(_state_config_value(state_config, "memory_filter", "broad") or "broad")

    def _broad_memory_examples(self, packet: FormalizationPacket) -> list[Any]:
        examples = self.trace_store.query_similar(
            list(packet.selected_preamble),
            limit=2,
            outcome="verified",
        )
        if not examples and self._normalized_claim_type(packet) == "mathlib_native":
            examples = self._mathlib_helper_memory_examples(packet, limit=2)
        return examples

    def _failure_focused_memory_examples(self, packet: FormalizationPacket) -> list[Any]:
        examples = self.trace_store.query_similar(
            list(packet.selected_preamble),
            limit=6,
            outcome=None,
        )
        if not examples and self._normalized_claim_type(packet) == "mathlib_native":
            examples = self._mathlib_helper_memory_examples(packet, limit=2)
        ranked_examples = sorted(
            examples,
            key=lambda trace: (
                trace.outcome == "verified",
                not trace.failure_class,
                -int(trace.repair_count or 0),
            ),
        )
        return ranked_examples[:2]

    def _subgoal_focused_memory_examples(self, packet: FormalizationPacket) -> list[Any]:
        if self._normalized_claim_type(packet) == "mathlib_native":
            examples = self._mathlib_helper_memory_examples(packet, limit=2)
            if examples:
                return examples
        return self._broad_memory_examples(packet)

    def _rescue_identifier_memory_examples(self, packet: FormalizationPacket) -> list[Any]:
        if self._normalized_claim_type(packet) == "mathlib_native":
            examples = self._mathlib_helper_memory_examples(packet, limit=1)
            if examples:
                return examples
        examples = self.trace_store.query_similar(
            list(packet.selected_preamble),
            limit=1,
            outcome="verified",
        )
        return examples

    def _mathlib_helper_memory_examples(
        self,
        packet: FormalizationPacket,
        *,
        limit: int,
    ) -> list[Any]:
        query = " ".join([packet.claim, packet.theorem_name])
        try:
            return self.trace_store.query_mathlib_helpers(
                self._proof_synthesizer_keywords(query),
                limit=limit,
            )
        except AttributeError:
            return []

    def _tool_specs_for_prompt(self, packet: FormalizationPacket) -> list[dict[str, Any]]:
        mathlib_native_mode = self._normalized_claim_type(packet) == "mathlib_native"
        allowed_tools = self._state_allowed_tool_names()
        if self._state_tool_limit_reached():
            allowed_tools = set()
        specs = []
        for spec in self.registry.list():
            if spec.name not in allowed_tools:
                continue
            # LSP search tools are exposed to the model only for mathlib-native
            # claims; preamble-definable claims should first exercise the local
            # indexed lemmas and bounded compile checks.
            if spec.name in MATHLIB_NATIVE_PROMPT_ONLY_TOOLS and not mathlib_native_mode:
                continue
            specs.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "args": spec.args,
                }
            )
        return specs

    def _build_mathlib_harness_prompt(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        state: dict[str, Any],
        retrieved_premises: list[dict[str, Any]],
        diagnostics: Any,
        code_actions: Any,
        prior_trace: list[ProverTraceStep],
        proof_sketch: dict[str, Any] | None = None,
    ) -> str:
        recent_steps = [
            {
                "turn": step.turn,
                "action_type": step.action_type,
                "tool_name": step.tool_name,
                "success": step.success,
                "tool_result": step.tool_result,
            }
            for step in prior_trace[-3:]
        ]
        # Sprint 23 Task 2: prefer enriched hover signature over thin leansearch payload.
        prompt_premises = [self._project_premise_for_prompt(p) for p in (retrieved_premises or [])]
        # Stage 1 Task 2: strengthened decomposition hint (actionable tactics) + concise
        # multi-step patterns (consider-this guidance). Aligns with lean4_proving skill:
        # structural decomposition only; trust harness premises; no long inventories.
        rules = [
            "Return one apply_tactic action.",
            "Prefer tactics that reference retrieved Mathlib premises.",
            "Do not rewrite the theorem body in this harness loop.",
        ]
        state_context = _state_prompt_context(
            current_state=getattr(self, "current_state", None),
            state_config=getattr(self, "current_state_config", None),
        )
        if state_context is not None:
            prompt_rules = state_context["prompt_rules"]
            guidance = prompt_rules.get("guidance")
            if guidance:
                rules.append(str(guidance))
        if self._goals_need_decomposition_hint(state.get("goals")):
            rules.append(
                "If goal has quantifiers (∀/∃) or conjuncts (∧/↔), start with "
                "intro (add hypotheses), obtain/cases (existentials), refine (premise "
                "with holes), or constructor (split) before retrieved premises."
            )
            rules.append(
                "For compact maximum goals, obtain the witness from the compact or "
                "extreme-value premise before refining the existential target."
            )
            rules.append(
                "For monotone bounded convergence goals, prefer the theorem whose "
                "conclusion already is `Tendsto`; wrap it in `Exists.intro` only "
                "when the goal is existential."
            )
            rules.append(
                "Consider patterns: 1. Quantified: `intro h; obtain ⟨x,hx⟩:=premise h; "
                "refine ⟨x,hx,_⟩`. 2. Conjunctive: `constructor; · exact p1; · refine p2`. "
                "3. Nested: `cases h with h1 h2; refine ...` (use Mathlib lemmas). "
                "4. ExistsMembership-Conjunctive: `obtain ⟨x, hxs, hP⟩ := premise hyps; "
                "exact ⟨x, hxs, hP, hQ⟩`."
            )
        # Stage 2 P1.B: premise-utilization protocol — teaches the model how to
        # consume the enriched fields the harness already supplies (full type
        # signature, detailed docstring, declaration location). General; no
        # claim-specific guidance.
        premise_utilization = [
            "If a premise's full_type_signature ends in your goal shape, prefer "
            "`exact <name> ...` or `apply <name>`.",
            "If a premise has form `(h : P) → ∃ x, Q x` and your goal is `∃ x, Q x`, "
            "use `obtain ⟨x, hx⟩ := <name> <hyp>` then close.",
            "Match a premise by detailed_docstring keywords against the operators "
            "and types in your current goal before guessing.",
            "Prefer premises whose declaration_location lies in a Mathlib namespace "
            "matching your goal's types.",
        ]
        prompt_payload = {
            "claim": packet.claim,
            "theorem_name": packet.theorem_name,
            "claim_type": "mathlib_native",
            "target": target.model_dump(mode="json"),
            "current_code": state.get("code"),
            "goals": state.get("goals"),
            "diagnostics": diagnostics,
            "code_actions": code_actions,
            "file_outline": state.get("file_outline"),
            "retrieved_premises": prompt_premises,
            "proof_sketch": proof_sketch,
            "candidate_tactics": (proof_sketch or {}).get("candidate_tactics", []),
            "synthesis_few_shots": self._proof_synthesizer.few_shots(),
            "recent_trace": recent_steps,
            "instructions": {
                "return_json_only": True,
                "only_allowed_tool": "apply_tactic",
                "use_retrieved_premises": True,
                "rules": rules,
                "candidate_tactic_rule": (
                    "If candidate_tactics is non-empty, try the first candidate that "
                    "mentions a retrieved premise before inventing a new tactic."
                ),
                "premise_utilization": premise_utilization,
            },
            "response_schema": {
                "action_type": "tool",
                "rationale": "string",
                "tool": {
                    "name": "apply_tactic",
                    "arguments": {"tactic": "Lean tactic"},
                },
            },
        }
        if state_context is not None:
            prompt_payload["state_context"] = state_context
            prompt_payload["instructions"]["state_prompt_rules"] = state_context["prompt_rules"]
            prompt_payload["instructions"]["state_memory_filter"] = state_context["memory_filter"]
        return json.dumps(
            prompt_payload,
            ensure_ascii=True,
            indent=2,
            default=str,
        )

    @staticmethod
    def _project_premise_for_prompt(premise: dict[str, Any]) -> dict[str, Any]:
        """Prefer ``full_type_signature``/``detailed_docstring`` over thin leansearch fields."""
        out = dict(premise)
        full_sig = premise.get("full_type_signature")
        if full_sig:
            out["statement"] = full_sig
        detailed_doc = premise.get("detailed_docstring")
        if detailed_doc and not premise.get("docstring"):
            out["docstring"] = detailed_doc
        return out

    @staticmethod
    def _goals_need_decomposition_hint(goals: Any) -> bool:
        if not goals:
            return False
        if isinstance(goals, str):
            text = goals
        else:
            try:
                text = "\n".join(str(g) for g in goals)
            except TypeError:
                text = str(goals)
        markers = (
            "∀",
            "∃",
            "∧",
            "↔",
            "IsCompact",
            "CompactSpace",
            "ContinuousOn",
            "IsMaxOn",
            "IsMinOn",
            "IsConstrainedMaximum",
            "Monotone",
            "BddAbove",
            "Set.range",
            "Tendsto",
            "atTop",
            "sSup",
            "ciSup",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _proof_synthesizer_keywords(text: str) -> list[str]:
        import re

        return [token for token in re.findall(r"[A-Za-z][A-Za-z0-9_']*", text) if len(token) > 3][
            :8
        ]

    def _metadata_tactic_hints(self, packet: FormalizationPacket) -> list[str]:
        from src.planner.retrieval import _entry_tactic_hints, _load_metadata

        hints: list[str] = []
        for entry in self._selected_preamble_entries(packet):
            metadata = _load_metadata(entry)
            for hint in _entry_tactic_hints(entry, metadata):
                normalized = hint.strip()
                if normalized and normalized not in hints:
                    hints.append(normalized)
        return hints

    def _first_turn_hints(self, packet: FormalizationPacket) -> list[str]:
        from src.planner.retrieval import _entry_tactic_hints, _load_metadata
        from src.preamble_library import PREAMBLE_LIBRARY

        hints: list[str] = []
        for name in ("fixed_point_theorem", "value_function"):
            if name not in packet.selected_preamble:
                continue
            entry = PREAMBLE_LIBRARY.get(name)
            if entry is None:
                continue
            for hint in _entry_tactic_hints(entry, _load_metadata(entry)):
                if hint not in hints:
                    hints.append(hint)
        return hints
