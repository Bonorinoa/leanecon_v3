"""Hybrid planner retrieval over the Preamble and episodic memory."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol

from src.config import EMBEDDING_MODEL
from src.memory import ProofTraceStore, trace_store
from src.memory.models import ProofTrace
from src.planner.models import MemoryTraceExample, PlannerContext, PreambleHit
from src.preamble_library import PREAMBLE_LIBRARY, PreambleEntry, rank_matching_preambles


class TextEmbedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode text into dense vectors."""


def _normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", token.lower())


def _tokenize(text: str) -> list[str]:
    return [_normalize_token(token) for token in re.findall(r"[A-Za-z0-9_'-]+", text.lower()) if _normalize_token(token)]


def _query_text(claim: str, theorem_stub: str | None = None) -> str:
    parts = [claim.strip()]
    if theorem_stub and theorem_stub.strip():
        parts.append(theorem_stub.strip())
    return "\n".join(part for part in parts if part)


class HashingTextEmbedder:
    """Dependency-free fallback embedder using hashed bag-of-words vectors."""

    def __init__(self, *, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in _tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:2], "big") % self.dimensions
                vector[index] += 1.0
            vectors.append(vector)
        return vectors


class SentenceTransformerEmbedder:
    """Sentence-transformers backed embedder."""

    def __init__(self, model_name: str = EMBEDDING_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, local_files_only=True)

    def encode(self, texts: list[str]) -> list[list[float]]:
        matrix = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, row)) for row in matrix]


def get_default_embedder() -> TextEmbedder:
    try:
        return SentenceTransformerEmbedder()
    except Exception:
        return HashingTextEmbedder()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _metadata_candidates(entry: PreambleEntry) -> tuple[Path, ...]:
    stem = entry.lean_path.with_suffix("")
    return (
        stem.with_suffix(".metadata.json"),
        stem.parent / "metadata.json",
    )


def _load_metadata(entry: PreambleEntry) -> dict[str, object]:
    for candidate in _metadata_candidates(entry):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
    return {}


def _entry_concepts(entry: PreambleEntry, metadata: dict[str, object]) -> list[str]:
    concepts = metadata.get("concepts")
    if isinstance(concepts, list):
        values = [str(value) for value in concepts]
        if values:
            return values
    planner_concepts = entry.planner_metadata.get("concepts")
    if isinstance(planner_concepts, list):
        return [str(value) for value in planner_concepts]
    return []


def _entry_related(entry: PreambleEntry, metadata: dict[str, object]) -> list[str]:
    related = metadata.get("related")
    if isinstance(related, list):
        values = [str(value) for value in related]
        if values:
            return values
    planner_related = entry.planner_metadata.get("related")
    if isinstance(planner_related, list):
        return [str(value) for value in planner_related]
    return []


def _entry_tactic_hints(entry: PreambleEntry, metadata: dict[str, object]) -> list[str]:
    hints = metadata.get("tactic_hints")
    if isinstance(hints, list):
        values = [str(value) for value in hints]
        if values:
            return values
    if entry.planner_tactic_hint:
        return [entry.planner_tactic_hint]
    return []


def _entry_textbook_source(entry: PreambleEntry, metadata: dict[str, object]) -> str | None:
    textbook_source = metadata.get("textbook_source")
    if isinstance(textbook_source, str) and textbook_source.strip():
        return textbook_source.strip()
    planner_source = entry.planner_metadata.get("textbook_source")
    if isinstance(planner_source, str) and planner_source.strip():
        return planner_source.strip()
    return None


def _entry_document(entry: PreambleEntry, metadata: dict[str, object]) -> str:
    fragments: list[str] = [
        entry.name,
        entry.lean_module,
        entry.description,
        " ".join(entry.keywords),
        " ".join(_entry_concepts(entry, metadata)),
        " ".join(entry.planner_proven_lemmas),
        " ".join(_entry_tactic_hints(entry, metadata)),
        " ".join(_entry_related(entry, metadata)),
        _entry_textbook_source(entry, metadata) or "",
    ]
    return "\n".join(fragment for fragment in fragments if fragment)


def _lexical_claim_score(
    claim: str,
    entry: PreambleEntry,
    metadata: dict[str, object],
    *,
    weighted_match_score: float,
) -> float:
    normalized = claim.lower()
    score = weighted_match_score
    concepts = _entry_concepts(entry, metadata)
    score += sum(2.0 for concept in concepts if concept.replace("_", " ") in normalized or concept in normalized)
    score += sum(1.0 for lemma in entry.planner_proven_lemmas if lemma.lower() in normalized)
    return score


def _trace_overlap(query: str, trace: ProofTrace) -> int:
    query_tokens = set(_tokenize(query))
    trace_tokens = set(_tokenize(trace.claim_text))
    return len(query_tokens & trace_tokens)


def infer_structure_tags(
    claim: str,
    *,
    preamble_names: list[str] | None = None,
    tactics: list[str] | None = None,
) -> list[str]:
    normalized = claim.lower()
    tags: list[str] = []
    if "bellman" in normalized or (preamble_names and "bellman_operator" in preamble_names):
        tags.append("bellman")
    if "contraction" in normalized or (preamble_names and "contraction_mapping" in preamble_names):
        tags.append("contraction")
    if "fixed point" in normalized or "fixed-point" in normalized or (preamble_names and "fixed_point_theorem" in preamble_names):
        tags.append("fixed_point")
    if "value function" in normalized or (preamble_names and "value_function" in preamble_names):
        tags.append("value_function")
    if "nash" in normalized or (preamble_names and "nash_existence" in preamble_names):
        tags.append("nash")
    if "equilibrium" in normalized:
        tags.append("equilibrium")
    if "optimiz" in normalized or (preamble_names and "constrained_optimization" in preamble_names):
        tags.append("optimization")
    if "kuhn" in normalized or "tucker" in normalized or (preamble_names and "kuhn_tucker" in preamble_names):
        tags.append("kuhn_tucker")
    if "preference" in normalized or (preamble_names and "continuous_preference" in preamble_names):
        tags.append("preference")
    for tactic in tactics or []:
        lowered = tactic.lower()
        if "fixedpoint" in lowered or "fixed_point" in lowered:
            tags.append("fixed_point")
        if "contraction" in lowered:
            tags.append("contraction")
        if "monotone" in lowered or "hvw" in lowered:
            tags.append("monotonicity")
    deduped: list[str] = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return deduped


def _trace_score(
    query: str,
    trace: ProofTrace,
    similarity: float,
    *,
    selected_preamble_names: list[str],
    query_structure_tags: list[str],
) -> float:
    overlap = _trace_overlap(query, trace)
    trace_preamble_names = set(trace.preamble_names)
    preamble_overlap = len(trace_preamble_names & set(selected_preamble_names))
    structure_overlap = len(
        set(query_structure_tags)
        & set(infer_structure_tags(trace.claim_text, preamble_names=trace.preamble_names, tactics=trace.tactic_sequence))
    )
    recency_bonus = 0.25 if trace.repair_count == 0 else 0.0
    return float(overlap) + (2.5 * preamble_overlap) + (1.5 * structure_overlap) + max(similarity, 0.0) * 3.0 + recency_bonus


def _trace_is_relevant(
    query: str,
    trace: ProofTrace,
    similarity: float,
    *,
    selected_preamble_names: list[str],
    query_structure_tags: list[str],
) -> bool:
    preamble_overlap = len(set(trace.preamble_names) & set(selected_preamble_names))
    if preamble_overlap == 0:
        return False
    overlap = _trace_overlap(query, trace)
    structure_overlap = len(
        set(query_structure_tags)
        & set(infer_structure_tags(trace.claim_text, preamble_names=trace.preamble_names, tactics=trace.tactic_sequence))
    )
    return overlap >= 2 or similarity >= 0.35 or (structure_overlap > 0 and similarity >= 0.2)


def _infer_subgoal_hint(trace: ProofTrace, shared_preamble_names: list[str], structure_tags: list[str]) -> str:
    if "bellman" in structure_tags and "contraction" in structure_tags:
        return "Reuse the retrieved Bellman lemma before expanding into any extra decomposition."
    if "nash" in structure_tags or "equilibrium" in structure_tags:
        return "Prefer the direct equilibrium witness lemma before introducing extra structure."
    if "optimization" in structure_tags or "kuhn_tucker" in structure_tags:
        return "Reuse the direct optimization certificate lemma before decomposing the claim."
    if shared_preamble_names:
        return f"Reuse the {', '.join(shared_preamble_names[:2])} lemma shape directly."
    return "Reuse the verified trace structure before specializing the claim."


def _sanitize_trace(
    trace: ProofTrace,
    *,
    selected_preamble_names: list[str],
) -> MemoryTraceExample:
    tactic_excerpt = trace.tactic_sequence[:2]
    stage_markers = [f"{stage}={status}" for stage, status in sorted(trace.stage_outcomes.items())[:2]]
    lesson_fragments = tactic_excerpt if tactic_excerpt else stage_markers
    lesson = "; ".join(lesson_fragments) if lesson_fragments else "Verified proof trace with reusable structure."
    shared_preamble_names = [name for name in trace.preamble_names if name in selected_preamble_names][:2]
    structure_tags = infer_structure_tags(
        trace.claim_text,
        preamble_names=trace.preamble_names,
        tactics=trace.tactic_sequence,
    )
    return MemoryTraceExample(
        claim_text=trace.claim_text,
        preamble_names=trace.preamble_names[:3],
        shared_preamble_names=shared_preamble_names,
        structure_tags=structure_tags,
        tactic_sequence=tactic_excerpt,
        outcome=trace.outcome,
        timestamp=trace.timestamp,
        lesson=lesson,
        subgoal_hint=_infer_subgoal_hint(trace, shared_preamble_names, structure_tags),
    )


def _hit_from_entry(entry: PreambleEntry, *, score: float, metadata: dict[str, object]) -> PreambleHit:
    return PreambleHit(
        name=entry.name,
        lean_module=entry.lean_module,
        score=round(score, 4),
        description=entry.description,
        concepts=_entry_concepts(entry, metadata),
        proven_lemmas=list(entry.planner_proven_lemmas),
        tactic_hints=_entry_tactic_hints(entry, metadata),
        textbook_source=_entry_textbook_source(entry, metadata),
        related=_entry_related(entry, metadata),
    )


def _compact_preamble_context(selected_preamble: list[PreambleHit]) -> str:
    lines: list[str] = []
    for hit in selected_preamble:
        lines.append(f"- {hit.name}: {hit.description}")
        if hit.proven_lemmas:
            lines.append(f"  lemmas: {', '.join(hit.proven_lemmas[:2])}")
        if hit.tactic_hints:
            lines.append(f"  tactics: {', '.join(hit.tactic_hints[:2])}")
    return "\n".join(lines)


def _compact_memory_context(few_shot_traces: list[MemoryTraceExample]) -> str:
    if not few_shot_traces:
        return ""
    trace = few_shot_traces[0]
    parts = [f"- {trace.claim_text}"]
    if trace.shared_preamble_names:
        parts.append(f"shared_preamble={', '.join(trace.shared_preamble_names)}")
    if trace.lesson:
        parts.append(f"lesson={trace.lesson}")
    return " | ".join(parts)


@dataclass
class PlannerRetrievalService:
    embedder: TextEmbedder | None = None
    trace_store: ProofTraceStore = trace_store

    def _resolve_embedder(self) -> TextEmbedder:
        if self.embedder is None:
            self.embedder = get_default_embedder()
        return self.embedder

    def retrieve_preamble(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        limit: int = 2,
    ) -> list[PreambleHit]:
        entries = list(PREAMBLE_LIBRARY.values())
        metadata_by_name = {entry.name: _load_metadata(entry) for entry in entries}
        selected: list[PreambleHit] = []
        seen: set[str] = set()

        for index, name in enumerate(preamble_names or []):
            entry = PREAMBLE_LIBRARY.get(name)
            if entry is None or entry.name in seen:
                continue
            seen.add(entry.name)
            selected.append(
                _hit_from_entry(
                    entry,
                    score=1000.0 - float(index),
                    metadata=metadata_by_name[entry.name],
                )
            )
            if len(selected) >= limit:
                return selected[:limit]

        query_text = _query_text(claim, theorem_stub)
        embedder = self._resolve_embedder()
        weighted_matches = {entry.name: float(score) for entry, score in rank_matching_preambles(query_text)}
        query_vector = embedder.encode([query_text])[0]
        remaining_entries = [entry for entry in entries if entry.name not in seen]
        if not remaining_entries:
            return selected[:limit]

        entry_vectors = embedder.encode([_entry_document(entry, metadata_by_name[entry.name]) for entry in remaining_entries])
        ranked: list[PreambleHit] = []
        for entry, vector in zip(remaining_entries, entry_vectors, strict=True):
            metadata = metadata_by_name[entry.name]
            lexical_score = _lexical_claim_score(
                query_text,
                entry,
                metadata,
                weighted_match_score=weighted_matches.get(entry.name, 0.0),
            )
            semantic_score = max(cosine_similarity(query_vector, vector), 0.0)
            total_score = lexical_score + semantic_score * 3.0
            if total_score <= 0:
                continue
            ranked.append(_hit_from_entry(entry, score=total_score, metadata=metadata))

        ranked.sort(key=lambda hit: (-hit.score, hit.name))
        for hit in ranked:
            if hit.name in seen:
                continue
            selected.append(hit)
            seen.add(hit.name)
            if len(selected) >= limit:
                break
        return selected[:limit]

    def retrieve_memory_traces(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        selected_preamble: list[PreambleHit],
        limit: int = 1,
        candidate_limit: int = 6,
    ) -> list[MemoryTraceExample]:
        if not selected_preamble or limit <= 0:
            return []

        embedder = self._resolve_embedder()
        query_text = _query_text(claim, theorem_stub)
        preamble_names = [hit.name for hit in selected_preamble]
        query_structure_tags = infer_structure_tags(query_text, preamble_names=preamble_names)
        candidates = self.trace_store.query_similar(
            preamble_names,
            limit=max(candidate_limit, limit),
            outcome="verified",
        )
        if not candidates:
            return []

        texts = [query_text, *[trace.claim_text for trace in candidates]]
        vectors = embedder.encode(texts)
        query_vector = vectors[0]
        ranked = sorted(
            zip(candidates, vectors[1:], strict=True),
            key=lambda item: _trace_score(
                query_text,
                item[0],
                cosine_similarity(query_vector, item[1]),
                selected_preamble_names=preamble_names,
                query_structure_tags=query_structure_tags,
            ),
            reverse=True,
        )
        sanitized: list[MemoryTraceExample] = []
        seen_claims: set[str] = set()
        for trace, vector in ranked:
            if trace.claim_text in seen_claims:
                continue
            similarity = cosine_similarity(query_vector, vector)
            if not _trace_is_relevant(
                query_text,
                trace,
                similarity,
                selected_preamble_names=preamble_names,
                query_structure_tags=query_structure_tags,
            ):
                continue
            seen_claims.add(trace.claim_text)
            sanitized.append(
                _sanitize_trace(
                    trace,
                    selected_preamble_names=preamble_names,
                )
            )
            if len(sanitized) >= limit:
                break
        return sanitized

    def build_context(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        preamble_limit: int = 2,
        memory_limit: int = 1,
    ) -> PlannerContext:
        selected_preamble = self.retrieve_preamble(
            claim,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
            limit=preamble_limit,
        )
        few_shot_traces = self.retrieve_memory_traces(
            claim,
            theorem_stub=theorem_stub,
            selected_preamble=selected_preamble,
            limit=memory_limit,
        )
        return PlannerContext(
            claim=claim,
            selected_preamble=selected_preamble,
            few_shot_traces=few_shot_traces,
            preamble_context=_compact_preamble_context(selected_preamble),
            memory_context=_compact_memory_context(few_shot_traces),
        )
