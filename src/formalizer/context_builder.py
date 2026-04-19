"""Build structured preamble context for formalization."""

from __future__ import annotations

from dataclasses import dataclass

from src.formalizer.models import FormalizerContext, PreambleContextEntry
from src.planner.models import PlannerPacket
from src.preamble_library import (
    build_preamble_imports,
    find_matching_preambles,
    get_preamble_entries,
    read_preamble_source,
)


@dataclass(frozen=True)
class BuiltFormalizerContext:
    context: FormalizerContext
    planner_packet: PlannerPacket | None


class FormalizerContextBuilder:
    """Build the runtime context passed to the formalizer model."""

    def build(self, claim: str, planner_packet: PlannerPacket | None = None) -> BuiltFormalizerContext:
        entries = self._resolve_entries(claim, planner_packet)
        context = FormalizerContext(
            claim=claim,
            plan_paragraph=planner_packet.plan_paragraph if planner_packet else "",
            textbook_defaults=list(planner_packet.textbook_defaults) if planner_packet else [],
            planner_subgoals=list(planner_packet.subgoals) if planner_packet else [],
            selected_preamble=[entry.name for entry in entries],
            imports=build_preamble_imports(entries),
            open_statements=[],
            preamble_entries=[self._entry_context(entry) for entry in entries],
        )
        return BuiltFormalizerContext(context=context, planner_packet=planner_packet)

    def _resolve_entries(self, claim: str, planner_packet: PlannerPacket | None) -> list:
        if planner_packet and planner_packet.selected_preamble:
            names = [hit.name for hit in planner_packet.selected_preamble]
            entries = get_preamble_entries(names)
            if entries:
                return entries
        return find_matching_preambles(claim)[:5]

    def _entry_context(self, entry) -> PreambleContextEntry:
        metadata = entry.planner_metadata
        tactic_hints: list[str] = []
        if entry.planner_tactic_hint:
            tactic_hints.append(entry.planner_tactic_hint)
        for hint in metadata.get("tactic_hints", []):
            hint_text = str(hint).strip()
            if hint_text and hint_text not in tactic_hints:
                tactic_hints.append(hint_text)
        return PreambleContextEntry(
            name=entry.name,
            lean_module=entry.lean_module,
            description=entry.description,
            definitions=list(entry.definitions),
            definition_signatures=list(entry.definition_signatures),
            proven_lemmas=list(entry.planner_proven_lemmas),
            theorem_template=entry.planner_theorem_template,
            tactic_hints=tactic_hints,
            textbook_source=str(metadata.get("textbook_source")) if metadata.get("textbook_source") else None,
            related=[str(value) for value in metadata.get("related", [])],
            source_excerpt=read_preamble_source(entry),
        )
