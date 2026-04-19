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


def _trace_score(query: str, trace: ProofTrace, similarity: float) -> float:
    query_tokens = set(_tokenize(query))
    trace_tokens = set(_tokenize(trace.claim_text))
    overlap = len(query_tokens & trace_tokens)
    preamble_overlap = len(query_tokens & {_normalize_token(name) for name in trace.preamble_names})
    return float(overlap + preamble_overlap) + max(similarity, 0.0) * 3.0


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
    query_tokens = set(_tokenize(query))
    trace_tokens = set(_tokenize(trace.claim_text))
    overlap = len(query_tokens & trace_tokens)
    trace_preamble_names = set(trace.preamble_names)
    preamble_overlap = len(trace_preamble_names & set(selected_preamble_names))
    structure_overlap = len(
        set(query_structure_tags)
        & set(infer_structure_tags(trace.claim_text, preamble_names=trace.preamble_names, tactics=trace.tactic_sequence))
    )
    recency_bonus = 0.25 if trace.repair_count == 0 else 0.0
    return float(overlap) + (2.5 * preamble_overlap) + (2.0 * structure_overlap) + max(similarity, 0.0) * 3.0 + recency_bonus


def _infer_subgoal_hint(trace: ProofTrace, shared_preamble_names: list[str], structure_tags: list[str]) -> str:
    if "bellman" in structure_tags and "contraction" in structure_tags:
        return "Mirror a Bellman decomposition: define the operator, prove the discounted inequality, then close the fixed-point/contraction step."
    if "nash" in structure_tags or "equilibrium" in structure_tags:
        return "Split the proof into hypotheses on strategy spaces, an equilibrium existence step, and the witness/is_nash conclusion."
    if "optimization" in structure_tags or "kuhn_tucker" in structure_tags:
        return "Separate feasibility, optimality inequalities, and complementary-slackness style conditions."
    if shared_preamble_names:
        return f"Reuse the {', '.join(shared_preamble_names[:2])} structure before introducing claim-specific hypotheses."
    return "Reuse the verified trace structure before specializing the hypotheses to the current claim."


def _sanitize_trace(
    trace: ProofTrace,
    *,
    selected_preamble_names: list[str],
    query_structure_tags: list[str],
) -> MemoryTraceExample:
    tactic_excerpt = trace.tactic_sequence[:3]
    stage_markers = [f"{stage}={status}" for stage, status in sorted(trace.stage_outcomes.items())[:3]]
    lesson_fragments = tactic_excerpt if tactic_excerpt else stage_markers
    lesson = "; ".join(lesson_fragments) if lesson_fragments else "Verified proof trace with reusable dynamic structure."
    shared_preamble_names = [name for name in trace.preamble_names if name in selected_preamble_names][:3]
    structure_tags = infer_structure_tags(
        trace.claim_text,
        preamble_names=trace.preamble_names,
        tactics=trace.tactic_sequence,
    )
    return MemoryTraceExample(
        claim_text=trace.claim_text,
        preamble_names=trace.preamble_names[:5],
        shared_preamble_names=shared_preamble_names,
        structure_tags=structure_tags,
        tactic_sequence=tactic_excerpt,
        outcome=trace.outcome,
        timestamp=trace.timestamp,
        lesson=lesson,
        subgoal_hint=_infer_subgoal_hint(trace, shared_preamble_names, structure_tags),
    )


@dataclass
class PlannerRetrievalService:
    embedder: TextEmbedder | None = None
    trace_store: ProofTraceStore = trace_store

    def _resolve_embedder(self) -> TextEmbedder:
        if self.embedder is None:
            self.embedder = get_default_embedder()
        return self.embedder

    def retrieve_preamble(self, claim: str, *, limit: int = 5) -> list[PreambleHit]:
        embedder = self._resolve_embedder()
        entries = list(PREAMBLE_LIBRARY.values())
        metadata_by_name = {entry.name: _load_metadata(entry) for entry in entries}
        weighted_matches = {entry.name: float(score) for entry, score in rank_matching_preambles(claim)}
        query_vector = embedder.encode([claim])[0]
        entry_vectors = embedder.encode([_entry_document(entry, metadata_by_name[entry.name]) for entry in entries])
        ranked: list[PreambleHit] = []
        for entry, vector in zip(entries, entry_vectors, strict=True):
            metadata = metadata_by_name[entry.name]
            lexical_score = _lexical_claim_score(
                claim,
                entry,
                metadata,
                weighted_match_score=weighted_matches.get(entry.name, 0.0),
            )
            semantic_score = max(cosine_similarity(query_vector, vector), 0.0)
            total_score = lexical_score + semantic_score * 3.0
            if total_score <= 0:
                continue
            ranked.append(
                PreambleHit(
                    name=entry.name,
                    lean_module=entry.lean_module,
                    score=round(total_score, 4),
                    description=entry.description,
                    concepts=_entry_concepts(entry, metadata),
                    proven_lemmas=list(entry.planner_proven_lemmas),
                    tactic_hints=_entry_tactic_hints(entry, metadata),
                    textbook_source=_entry_textbook_source(entry, metadata),
                    related=_entry_related(entry, metadata),
                )
            )
        ranked.sort(key=lambda hit: (-hit.score, hit.name))
        return ranked[:limit]

    def retrieve_memory_traces(
        self,
        claim: str,
        *,
        selected_preamble: list[PreambleHit],
        limit: int = 2,
        candidate_limit: int = 10,
    ) -> list[MemoryTraceExample]:
        embedder = self._resolve_embedder()
        preamble_names = [hit.name for hit in selected_preamble]
        query_structure_tags = infer_structure_tags(claim, preamble_names=preamble_names)
        candidates = self.trace_store.query_similar(
            preamble_names,
            limit=max(candidate_limit, limit),
            outcome="verified",
        )
        if len(candidates) < limit:
            seen_ids = {trace.claim_id for trace in candidates}
            for trace in self.trace_store.list_recent(limit=candidate_limit, outcome="verified"):
                if trace.claim_id not in seen_ids:
                    candidates.append(trace)
                    seen_ids.add(trace.claim_id)
        if not candidates:
            return []

        texts = [claim, *[trace.claim_text for trace in candidates]]
        vectors = embedder.encode(texts)
        query_vector = vectors[0]
        ranked = sorted(
            zip(candidates, vectors[1:], strict=True),
            key=lambda item: _trace_score(
                claim,
                item[0],
                cosine_similarity(query_vector, item[1]),
                selected_preamble_names=preamble_names,
                query_structure_tags=query_structure_tags,
            ),
            reverse=True,
        )
        sanitized: list[MemoryTraceExample] = []
        seen_claims: set[str] = set()
        for trace, _vector in ranked:
            if trace.claim_text in seen_claims:
                continue
            seen_claims.add(trace.claim_text)
            sanitized.append(
                _sanitize_trace(
                    trace,
                    selected_preamble_names=preamble_names,
                    query_structure_tags=query_structure_tags,
                )
            )
            if len(sanitized) >= limit:
                break
        return sanitized

    def build_context(
        self,
        claim: str,
        *,
        preamble_limit: int = 5,
        memory_limit: int = 2,
    ) -> PlannerContext:
        selected_preamble = self.retrieve_preamble(claim, limit=preamble_limit)
        few_shot_traces = self.retrieve_memory_traces(
            claim,
            selected_preamble=selected_preamble,
            limit=memory_limit,
        )
        preamble_lines: list[str] = []
        for hit in selected_preamble:
            preamble_lines.append(
                "\n".join(
                    [
                        f"- {hit.name} ({hit.lean_module})",
                        f"  description: {hit.description}",
                        f"  concepts: {', '.join(hit.concepts) if hit.concepts else 'n/a'}",
                        f"  proven_lemmas: {', '.join(hit.proven_lemmas) if hit.proven_lemmas else 'n/a'}",
                        f"  tactic_hints: {', '.join(hit.tactic_hints) if hit.tactic_hints else 'n/a'}",
                        f"  textbook_source: {hit.textbook_source or 'n/a'}",
                        f"  related: {', '.join(hit.related) if hit.related else 'n/a'}",
                    ]
                )
            )
        memory_lines: list[str] = []
        for index, trace in enumerate(few_shot_traces, start=1):
            memory_lines.append(
                "\n".join(
                    [
                        f"Example {index}: {trace.claim_text}",
                        f"  shared_preamble: {', '.join(trace.shared_preamble_names) if trace.shared_preamble_names else 'n/a'}",
                        f"  structure_tags: {', '.join(trace.structure_tags) if trace.structure_tags else 'n/a'}",
                        f"  preamble: {', '.join(trace.preamble_names) if trace.preamble_names else 'n/a'}",
                        f"  tactics: {', '.join(trace.tactic_sequence) if trace.tactic_sequence else 'n/a'}",
                        f"  lesson: {trace.lesson}",
                        f"  subgoal_hint: {trace.subgoal_hint or 'n/a'}",
                    ]
                )
            )
        return PlannerContext(
            claim=claim,
            selected_preamble=selected_preamble,
            few_shot_traces=few_shot_traces,
            preamble_context="\n\n".join(preamble_lines),
            memory_context="\n\n".join(memory_lines),
        )
