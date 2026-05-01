"""Deterministic proof-synthesis helpers for mathlib-native proving.

The synthesizer builds compact proof sketches, detects premise usage in tactics,
and proposes conservative helper-lemma actions. It does not call providers or
Lean; execution remains owned by the prover harness.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from src.formalizer.models import FormalizationPacket
from src.prover.models import ProverAction, ProverTarget, ProverToolInvocation


MATHLIB_SYNTHESIS_FEW_SHOTS: tuple[dict[str, Any], ...] = (
    {
        "name": "fixed_point_unique",
        "goal_shape": "unique fixed point for a contraction",
        "premise_pattern": "contraction_has_unique_fixedPoint : ... -> UniqueFixedPoint ...",
        "tactic_shape": "exact contraction_has_unique_fixedPoint h_contracting",
        "lesson": "Prefer the retrieved fixed-point theorem directly when its conclusion matches.",
    },
    {
        "name": "compact_extreme_value",
        "goal_shape": "existential maximizer on a compact set",
        "premise_pattern": "IsCompact.exists_isMaxOn / ContinuousOn.exists_isMaxOn'",
        "tactic_shape": (
            "obtain \u27e8x, hx, hmax\u27e9 := hcompact.exists_isMaxOn hnonempty hcontinuous\n"
            "exact \u27e8x, hx, hmax\u27e9"
        ),
        "lesson": "Obtain the witness from the compactness premise, then refine the existential goal.",
    },
    {
        "name": "monotone_bounded_convergence",
        "goal_shape": "monotone sequence bounded above tends to a limit",
        "premise_pattern": "tendsto_atTop_ciSup / Real.tendsto_of_bddAbove_monotone",
        "tactic_shape": (
            "exact \u27e8_, tendsto_atTop_ciSup hmono hbdd\u27e9\n"
            "-- or: exact Real.tendsto_of_bddAbove_monotone hbdd hmono"
        ),
        "lesson": "Use the theorem whose conclusion is already Tendsto; wrap only if the goal is existential.",
    },
)


@dataclass(frozen=True)
class ProofSketch:
    strategy: str
    likely_premises: list[str] = field(default_factory=list)
    subgoal_order: list[str] = field(default_factory=list)
    tactic_shape: str = ""
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PremiseMatch:
    referenced_premises: list[str] = field(default_factory=list)
    top3_match: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.referenced_premises)


class ProofSynthesizer:
    """Small, model-agnostic synthesis helper for proof prompts and metrics."""

    def few_shots(self) -> list[dict[str, Any]]:
        return [dict(item) for item in MATHLIB_SYNTHESIS_FEW_SHOTS[:3]]

    def build_sketch(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        state: dict[str, Any],
        premises: list[dict[str, Any]],
    ) -> ProofSketch:
        goal_text = _join_text(state.get("goals"))
        premise_names = self._rank_premises(goal_text=goal_text, premises=premises)[:4]
        plan = str(getattr(packet, "planner_plan_paragraph", "") or "").strip()
        subgoals = [
            str(item).strip()
            for item in (getattr(packet, "planner_subgoals", []) or [])
            if str(item).strip()
        ][:4]
        topic = _topic_from_text("\n".join([target.statement, goal_text, plan]))
        strategy = plan or self._default_strategy(topic)
        return ProofSketch(
            strategy=strategy,
            likely_premises=premise_names,
            subgoal_order=subgoals or [target.statement],
            tactic_shape=self._tactic_shape(topic),
        )

    def premise_match(
        self,
        tactic: str,
        premises: list[dict[str, Any]],
    ) -> PremiseMatch:
        if not tactic or not premises:
            return PremiseMatch()
        referenced: list[str] = []
        for premise in premises:
            name = str(premise.get("name") or "").strip()
            if name and _mentions_name(tactic, name):
                referenced.append(name)
        top3 = {
            str(p.get("name") or "").strip()
            for p in premises[:3]
            if str(p.get("name") or "").strip()
        }
        return PremiseMatch(
            referenced_premises=referenced,
            top3_match=any(name in top3 for name in referenced),
        )

    def helper_lemma_action(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        state: dict[str, Any],
        sketch: ProofSketch | None,
        premises: list[dict[str, Any]],
        index: int,
    ) -> ProverAction | None:
        del premises
        candidates = [
            str(item).strip()
            for item in ((sketch.subgoal_order if sketch else []) or [])
            if str(item).strip()
        ]
        candidates.append(target.statement)
        statement = next((item for item in candidates if _looks_like_lean_prop(item)), "")
        if not statement:
            goal_text = _join_text(state.get("goals"))
            statement = _goal_conclusion(goal_text)
        if not statement:
            return None
        lemma_name = f"apollo_{packet.theorem_name}_synth_{index}"
        return ProverAction(
            action_type="decompose",
            rationale="Extract a stalled mathlib-native goal into a verified helper lemma.",
            decomposition_statement=statement,
            decomposition_name=lemma_name,
            tool=ProverToolInvocation(name="apply_tactic", arguments={"tactic": "exact?"}),
        )

    def _rank_premises(self, *, goal_text: str, premises: list[dict[str, Any]]) -> list[str]:
        goal_tokens = _tokens(goal_text)
        scored: list[tuple[int, str]] = []
        for premise in premises:
            name = str(premise.get("name") or "").strip()
            if not name:
                continue
            haystack = " ".join(
                str(premise.get(key) or "")
                for key in ("name", "statement", "full_type_signature", "docstring", "detailed_docstring")
            )
            score = len(goal_tokens & _tokens(haystack))
            scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored]

    @staticmethod
    def _default_strategy(topic: str) -> str:
        if topic == "compact_max":
            return "Obtain a maximizer witness from the compact extreme-value premise, then refine the existential target."
        if topic == "monotone_convergence":
            return "Use the monotone convergence theorem whose conclusion is Tendsto, wrapping it in an existential witness if needed."
        if topic == "fixed_point":
            return "Apply the retrieved fixed-point uniqueness theorem directly when its conclusion matches the goal."
        return "Match the current goal against retrieved premise conclusions, then use exact/apply/refine with local hypotheses."

    @staticmethod
    def _tactic_shape(topic: str) -> str:
        if topic == "compact_max":
            return "obtain \u27e8x, hx, hmax\u27e9 := <compact_max_premise> ...; exact \u27e8x, hx, hmax\u27e9"
        if topic == "monotone_convergence":
            return "exact \u27e8_, tendsto_atTop_ciSup hmono hbdd\u27e9"
        if topic == "fixed_point":
            return "exact contraction_has_unique_fixedPoint <hypotheses>"
        return "exact <matching_premise> <local_hypotheses>"


def _join_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return "\n".join(str(item) for item in value)
    except TypeError:
        return str(value)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_']*", text)}


def _mentions_name(tactic: str, name: str) -> bool:
    if not name:
        return False
    escaped = re.escape(name)
    if re.search(rf"(?<![A-Za-z0-9_.']){escaped}(?![A-Za-z0-9_'])", tactic):
        return True
    short = name.split(".")[-1]
    if short and short != name:
        return re.search(rf"(?<![A-Za-z0-9_.']){re.escape(short)}(?![A-Za-z0-9_'])", tactic) is not None
    return False


def _topic_from_text(text: str) -> str:
    lowered = text.lower()
    if any(token in text for token in ("IsMaxOn", "IsMinOn", "IsCompact", "IsConstrainedMaximum")):
        return "compact_max"
    if "tendsto" in lowered or any(token in text for token in ("Monotone", "BddAbove", "ciSup")):
        return "monotone_convergence"
    if "fixed" in lowered or "ContractingWith" in text:
        return "fixed_point"
    return "general"


def _looks_like_lean_prop(text: str) -> bool:
    return any(marker in text for marker in (" : ", "→", "∀", "∃", "=", "≤", "<", "Tendsto", "Is"))


def _goal_conclusion(goal_text: str) -> str:
    if "⊢" in goal_text:
        return goal_text.split("⊢")[-1].strip()
    return goal_text.strip()
