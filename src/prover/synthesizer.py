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
    candidate_tactics: list[str] = field(default_factory=list)
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


@dataclass(frozen=True)
class ResolvedPremise:
    raw_name: str
    lean_name: str | None
    statement: str
    source: str
    rank: int
    resolved: bool
    resolution_method: str
    failure_reason: str | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "name": self.raw_name,
            "resolved_name": self.lean_name,
            "statement": self.statement,
            "source": self.source,
            "rank": self.rank,
            "resolved": self.resolved,
            "resolution_method": self.resolution_method,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class TacticCandidate:
    tactic: str
    premise_name: str | None
    origin: str
    priority: int
    expected_effect: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProofSynthesizer:
    """Small, model-agnostic synthesis helper for proof prompts and metrics."""

    def few_shots(self) -> list[dict[str, Any]]:
        return [dict(item) for item in MATHLIB_SYNTHESIS_FEW_SHOTS[:3]]

    def resolve_premises(self, premises: list[dict[str, Any]]) -> list[ResolvedPremise]:
        resolved: list[ResolvedPremise] = []
        for index, premise in enumerate(premises):
            raw_name = str(premise.get("name") or "").strip()
            if not raw_name:
                continue
            statement = str(
                premise.get("full_type_signature")
                or premise.get("statement")
                or premise.get("type")
                or ""
            )
            source = str(premise.get("source") or premise.get("retrieval_source") or "")
            lean_name, method, failure = _resolve_premise_name(raw_name, premise)
            resolved.append(
                ResolvedPremise(
                    raw_name=raw_name,
                    lean_name=lean_name,
                    statement=statement,
                    source=source,
                    rank=index + 1,
                    resolved=lean_name is not None,
                    resolution_method=method,
                    failure_reason=failure,
                )
            )
        return resolved

    def premise_prompt_records(
        self,
        premises: list[dict[str, Any]],
        resolved_premises: list[ResolvedPremise],
    ) -> list[dict[str, Any]]:
        resolved_by_raw = {item.raw_name: item for item in resolved_premises}
        records: list[dict[str, Any]] = []
        for premise in premises:
            record = dict(premise)
            raw_name = str(record.get("name") or "").strip()
            resolved = resolved_by_raw.get(raw_name)
            if resolved is not None:
                record.update(resolved.to_prompt_dict())
            records.append(record)
        return records

    def build_sketch(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        state: dict[str, Any],
        premises: list[dict[str, Any]],
    ) -> ProofSketch:
        goal_text = _join_text(state.get("goals"))
        ranked_premises = self._rank_premises(goal_text=goal_text, premises=premises)
        premise_names = ranked_premises[:4]
        resolved = [
            premise
            for premise in self.resolve_premises(premises)
            if premise.resolved and premise.lean_name
        ]
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
            candidate_tactics=self._candidate_tactics(
                topic=topic,
                goal_text=goal_text,
                premise_names=[premise.lean_name or premise.raw_name for premise in resolved[:4]]
                or premise_names,
            ),
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
            resolved_name = str(premise.get("resolved_name") or "").strip()
            if name and (
                _mentions_name(tactic, name)
                or (resolved_name and _mentions_name(tactic, resolved_name))
            ):
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

    def tactic_candidates(
        self,
        *,
        state: dict[str, Any],
        premises: list[ResolvedPremise],
        limit: int = 6,
    ) -> list[TacticCandidate]:
        goal_text = _join_text(state.get("goals"))
        topic = _topic_from_text(goal_text)
        resolved = [premise for premise in premises if premise.resolved and premise.lean_name]
        if not resolved:
            return []
        tactics = self._candidate_tactics(
            topic=topic,
            goal_text=goal_text,
            premise_names=[premise.lean_name or premise.raw_name for premise in resolved[:4]],
        )
        raw_by_lean = {
            premise.lean_name or premise.raw_name: premise.raw_name for premise in resolved
        }
        candidates: list[TacticCandidate] = []
        for index, tactic in enumerate(tactics[:limit]):
            premise_name = next(
                (raw for lean_name, raw in raw_by_lean.items() if _mentions_name(tactic, lean_name)),
                None,
            )
            candidates.append(
                TacticCandidate(
                    tactic=tactic,
                    premise_name=premise_name,
                    origin="resolved_premise_micro_search",
                    priority=index + 1,
                    expected_effect=_expected_effect(tactic, goal_text),
                )
            )
        return candidates

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
        goal_text = _join_text(state.get("goals"))
        goal_statement = _goal_conclusion(goal_text)
        candidates = [goal_statement, target.statement]
        candidates.extend(
            str(item).strip()
            for item in ((sketch.subgoal_order if sketch else []) or [])
            if str(item).strip()
        )
        statement = next((item for item in candidates if _looks_like_lean_prop(item)), "")
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

    @staticmethod
    def _candidate_tactics(
        *,
        topic: str,
        goal_text: str,
        premise_names: list[str],
    ) -> list[str]:
        if not premise_names:
            return []
        hypotheses = _hypotheses_by_shape(goal_text)
        candidates: list[str] = []
        for name in premise_names[:3]:
            if topic == "monotone_convergence":
                args = _ordered_args(
                    hypotheses,
                    ("Monotone", "BddAbove", "Bounded"),
                )
                if args:
                    exact_tactic = f"exact {name} {' '.join(args)}"
                    exists_tactic = f"refine ⟨_, {name} {' '.join(args)}⟩"
                    if "∃" in goal_text:
                        candidates.extend([exists_tactic, exact_tactic])
                    else:
                        candidates.extend([exact_tactic, exists_tactic])
            if topic == "compact_max":
                args = _ordered_args(
                    hypotheses,
                    ("IsCompact", "Nonempty", "ContinuousOn", "Continuous"),
                )
                if args:
                    candidates.append(f"apply {name}")
            candidates.extend(
                [
                    f"exact {name}",
                    f"simpa using {name}",
                    f"apply {name}",
                ]
            )
        return _dedupe_preserve_order(candidates)[:6]


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


_CONTRACTING_NAMESPACE_NAMES: frozenset[str] = frozenset(
    {
        "fixedPoint",
        "efixedPoint",
        "exists_fixedPoint",
        "exists_fixedPoint'",
        "fixedPoint_isFixedPt",
        "fixedPoint_unique",
        "efixedPoint_isFixedPt",
        "efixedPoint_isFixedPt'",
    }
)


def _resolve_premise_name(
    raw_name: str,
    premise: dict[str, Any],
) -> tuple[str | None, str, str | None]:
    if "." in raw_name:
        return raw_name, "already_qualified", None
    file_path = str(premise.get("file_path") or "")
    if (
        file_path.endswith("Mathlib/Topology/MetricSpace/Contracting.lean")
        or file_path.endswith("Topology/MetricSpace/Contracting.lean")
    ) and raw_name in _CONTRACTING_NAMESPACE_NAMES:
        return f"ContractingWith.{raw_name}", "mathlib_file_namespace", None
    if raw_name in {"fixedPoint_isFixedPt", "fixedPoint_unique"}:
        return f"ContractingWith.{raw_name}", "known_mathlib_alias", None
    if raw_name.startswith("exists_fixedPoint") or raw_name.startswith("efixedPoint"):
        return f"ContractingWith.{raw_name}", "known_mathlib_alias", None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", raw_name):
        return raw_name, "raw_global_candidate", None
    return None, "unresolved", "not_a_lean_identifier"


def _expected_effect(tactic: str, goal_text: str) -> str:
    if tactic.startswith("refine") and "∃" in goal_text:
        return "introduce existential witness and reduce proof obligations"
    if tactic.startswith("exact"):
        return "close goal if premise conclusion matches"
    if tactic.startswith("apply"):
        return "reduce goal to premise hypotheses"
    if tactic.startswith("simpa"):
        return "close goal modulo simplification"
    return "attempt tactic generated from resolved premise"


def _hypotheses_by_shape(goal_text: str) -> dict[str, list[str]]:
    hypotheses: dict[str, list[str]] = {}
    for line in goal_text.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*(.+)", line)
        if not match:
            continue
        name, type_text = match.groups()
        for token in _tokens(type_text):
            hypotheses.setdefault(token.lower(), []).append(name)
        for marker in ("Monotone", "BddAbove", "Bounded", "IsCompact", "Nonempty", "ContinuousOn", "Continuous"):
            if marker in type_text:
                hypotheses.setdefault(marker.lower(), []).append(name)
    return hypotheses


def _ordered_args(hypotheses: dict[str, list[str]], shapes: tuple[str, ...]) -> list[str]:
    args: list[str] = []
    seen: set[str] = set()
    for shape in shapes:
        for name in hypotheses.get(shape.lower(), []):
            if name not in seen:
                seen.add(name)
                args.append(name)
                break
    return args


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


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
