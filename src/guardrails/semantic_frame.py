"""Semantic-frame faithfulness scoring stub for v3."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]+")
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.]+)\s*$", re.MULTILINE)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_HAVE_RE = re.compile(r"\bhave\s+[A-Za-z0-9_']+\s*:\s*(.+?)\s*:=\s*by", re.DOTALL)

FRAME_KEYWORDS = {
    "bellman": {"bellman", "value", "function", "operator", "dynamic"},
    "welfare": {"pareto", "welfare", "social", "efficient"},
    "consumer": {"utility", "demand", "budget", "consumer", "roy"},
    "producer": {"production", "cost", "profit", "elasticity", "capital", "labor"},
    "game": {"game", "equilibrium", "payoff", "strategy", "nash", "subgame"},
}


@dataclass(frozen=True)
class SemanticFrame:
    concepts: tuple[str, ...]
    imports: tuple[str, ...]
    quantifier_shape: str


def _tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(text)}


def _quantifier_shape(text: str) -> str:
    lowered = text.lower()
    existential_positions = [position for position in (lowered.find("there exists"), text.find("∃")) if position != -1]
    universal_positions = [position for position in (lowered.find("for all"), text.find("∀")) if position != -1]
    if existential_positions and universal_positions:
        return "existential" if min(existential_positions) < min(universal_positions) else "universal"
    if existential_positions:
        return "existential"
    if universal_positions:
        return "universal"
    return "implicit"


def extract_claim_frame(claim: str) -> SemanticFrame:
    tokens = _tokenize(claim)
    concepts: set[str] = set()
    for label, keywords in FRAME_KEYWORDS.items():
        if tokens & keywords:
            concepts.add(label)
            concepts.update(sorted(tokens & keywords))
    if not concepts:
        concepts = set(sorted(tokens)[:5])
    return SemanticFrame(
        concepts=tuple(sorted(concepts)),
        imports=(),
        quantifier_shape=_quantifier_shape(claim),
    )


def extract_stub_frame(lean_stub: str) -> SemanticFrame:
    tokens = _tokenize(lean_stub)
    imports = tuple(sorted(match.group(1) for match in _IMPORT_RE.finditer(lean_stub)))
    for import_name in imports:
        for segment in import_name.split("."):
            for piece in _CAMEL_RE.sub(" ", segment).split():
                tokens.add(piece.lower())
    concepts: set[str] = set()
    for label, keywords in FRAME_KEYWORDS.items():
        if tokens & keywords:
            concepts.add(label)
            concepts.update(sorted(keywords))
    return SemanticFrame(
        concepts=tuple(sorted(concepts)),
        imports=imports,
        quantifier_shape=_quantifier_shape(lean_stub),
    )


def _structural_match_score(claim_shape: str, stub_shape: str) -> float:
    if claim_shape == stub_shape:
        return 1.0
    if claim_shape == "implicit" and stub_shape == "universal":
        return 1.0
    if {claim_shape, stub_shape} == {"implicit", "existential"}:
        return 0.75
    return 0.5


def _tactic_alignment_score(lean_stub: str) -> float:
    matches = [match.strip() for match in _HAVE_RE.findall(lean_stub)]
    if not matches:
        return 0.5
    nontrivial = [match for match in matches if " ".join(match.split()) not in {"True", "False", "Prop", "1 = 1"}]
    if not nontrivial:
        return 0.5
    return 1.0


def semantic_faithfulness_score(
    claim: str,
    lean_stub: str,
    *,
    human_review_override: float = 0.0,
) -> dict[str, object]:
    claim_frame = extract_claim_frame(claim)
    stub_frame = extract_stub_frame(lean_stub)
    claim_concepts = set(claim_frame.concepts)
    stub_concepts = set(stub_frame.concepts)
    concept_overlap = len(claim_concepts & stub_concepts)
    coverage = concept_overlap / max(len(claim_concepts), 1)
    structural = _structural_match_score(claim_frame.quantifier_shape, stub_frame.quantifier_shape)
    primitive = 1.0 if stub_frame.imports else 0.3
    tactic_alignment = _tactic_alignment_score(lean_stub)
    weighted = (
        coverage * 0.30
        + structural * 0.25
        + primitive * 0.20
        + tactic_alignment * 0.15
        + max(0.0, min(human_review_override, 1.0)) * 0.10
    )
    score = round(weighted * 5, 3)
    return {
        "score": score,
        "coverage": round(coverage, 3),
        "structural_isomorphism": round(structural, 3),
        "primitive_faithfulness": round(primitive, 3),
        "claim_frame": asdict(claim_frame),
        "stub_frame": asdict(stub_frame),
        "needs_human_review": 4.0 <= score < 4.5,
        "passes_gate": score >= 4.5,
    }
