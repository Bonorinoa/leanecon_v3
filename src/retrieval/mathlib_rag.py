"""Harness-owned Mathlib premise retrieval (Sprint 21).

Loads a lightweight JSONL premise catalog and ranks entries against a goal
state using a hybrid lexical + cosine scorer. Deterministic, model-agnostic,
and zero-cost at inference time.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from src.planner.retrieval import (
    TextEmbedder,
    cosine_similarity,
    get_default_embedder,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_PATH = REPO_ROOT / "data" / "mathlib_rag_seed.jsonl"
DEFAULT_CACHE_PATH = REPO_ROOT / "lean_workspace" / ".cache" / "mathlib_rag.jsonl"

_SYMBOL_ALIASES: dict[str, str] = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "^": "pow",
    "≤": "le",
    "<=": "le",
    "<": "lt",
    "≥": "ge",
    ">=": "ge",
    ">": "gt",
    "=": "eq",
    "≠": "ne",
    "∘": "comp",
    "∪": "union",
    "∩": "inter",
    "ℕ": "nat",
    "ℝ": "real",
    "ℤ": "int",
    "ℚ": "rat",
    "λ": "fun",
    "∀": "forall",
    "∃": "exists",
}


@dataclass(frozen=True)
class Premise:
    name: str
    score: float = 0.0
    statement: str | None = None
    docstring: str | None = None
    file_path: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, (), [])}


def _normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def _split_camel(segment: str) -> list[str]:
    return re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", segment)


def _split_name(name: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"[._]", name):
        tokens.extend(_split_camel(part))
    return [_normalize_token(t) for t in tokens if _normalize_token(t)]


def _extract_query_tokens(goal_state: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9_'-]+", goal_state):
        norm = _normalize_token(raw)
        if norm:
            tokens.add(norm)
    for camel in _split_name(goal_state):
        if camel:
            tokens.add(camel)
    for symbol, alias in _SYMBOL_ALIASES.items():
        if symbol in goal_state:
            tokens.add(alias)
    return tokens


def _premise_text(premise: Premise) -> str:
    parts = [premise.name]
    if premise.statement:
        parts.append(premise.statement)
    if premise.docstring:
        parts.append(premise.docstring)
    parts.extend(premise.tags)
    return "\n".join(parts)


def _tag_tokens(premise: Premise) -> set[str]:
    tokens = {_normalize_token(t) for t in premise.tags}
    tokens.update(_split_name(premise.name))
    return {t for t in tokens if t}


class MathlibRAG:
    """Rank mathlib premises against a goal state.

    The index is a JSONL catalog (one premise per line) authored at
    ``data/mathlib_rag_seed.jsonl``. On first use it is copied to
    ``lean_workspace/.cache/mathlib_rag.jsonl`` so that later sprints can
    extend the cache without mutating the repo seed.
    """

    _NAME_WEIGHT = 0.75
    _TAG_BONUS_STEP = 0.05
    _TAG_BONUS_CAP = 0.15
    _COSINE_WEIGHT = 0.10

    def __init__(
        self,
        *,
        seed_path: Path | None = None,
        index_path: Path | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.seed_path = Path(seed_path) if seed_path is not None else DEFAULT_SEED_PATH
        self.index_path = Path(index_path) if index_path is not None else DEFAULT_CACHE_PATH
        self._embedder = embedder or get_default_embedder()
        self._lock = Lock()
        self._premises: list[Premise] = []
        self._premise_vectors: list[list[float]] = []
        self._loaded = False

    # ---- index management ---------------------------------------------------

    @property
    def _hash_sidecar_path(self) -> Path:
        return self.index_path.with_suffix(self.index_path.suffix + ".sha256")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def is_stale(self) -> bool:
        if not self.index_path.exists():
            return True
        if not self.seed_path.exists():
            return False
        # mtime is the cheap check; falls through to a content-hash compare so
        # that ``git reset`` / rebases (which can leave the seed mtime older
        # than the cache mtime despite different bytes) still trigger a rebuild.
        if self.seed_path.stat().st_mtime > self.index_path.stat().st_mtime:
            return True
        sidecar = self._hash_sidecar_path
        if not sidecar.exists():
            return True
        try:
            stored_hash = sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        return self._file_sha256(self.seed_path) != stored_hash

    def rebuild(self) -> None:
        with self._lock:
            self._materialize_index()
            self._premises = []
            self._premise_vectors = []
            self._loaded = False

    def _materialize_index(self) -> None:
        if not self.seed_path.exists():
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.seed_path, self.index_path)
        # Write the content hash alongside the cache so future is_stale() calls
        # can detect seed changes that mtime alone would miss.
        self._hash_sidecar_path.write_text(
            self._file_sha256(self.seed_path), encoding="utf-8"
        )

    def _load_if_needed(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.is_stale():
                self._materialize_index()
            source = self.index_path if self.index_path.exists() else self.seed_path
            premises = list(self._read_jsonl(source))
            self._premises = premises
            self._premise_vectors = (
                self._embedder.encode([_premise_text(p) for p in premises]) if premises else []
            )
            self._loaded = True

    @staticmethod
    def _read_jsonl(path: Path) -> list[Premise]:
        if not path.exists():
            return []
        entries: list[Premise] = []
        skipped = 0
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                entries.append(
                    Premise(
                        name=str(record.get("name") or f"premise_{line_no}"),
                        statement=record.get("type_signature") or record.get("statement"),
                        docstring=record.get("docstring") or None,
                        file_path=record.get("file_path") or None,
                        tags=tuple(record.get("tags") or ()),
                        dependencies=tuple(record.get("dependencies") or ()),
                    )
                )
        if skipped:
            # Stage 2 H.3: surface silent index degradation as a single startup
            # audit event. Loading remains resilient; the audit just makes drift
            # observable in benchmark traces.
            try:
                from src.observability import AuditEvent, log_event

                log_event(
                    AuditEvent(
                        stage="retrieval",
                        event_type="mathlib_rag_jsonl_skip",
                        provider="mathlib_rag",
                        model="local",
                        success=False,
                        error_code="jsonl_decode_error",
                        error_message=(
                            f"Skipped {skipped} malformed line(s) while loading {path.name}"
                        ),
                        metadata={"path": str(path), "skipped_lines": int(skipped)},
                    )
                )
            except Exception:
                pass
        return entries

    # ---- scoring ------------------------------------------------------------

    def _score(
        self,
        premise: Premise,
        query_tokens: set[str],
        query_vector: list[float],
        premise_vector: list[float],
    ) -> float:
        name_tokens = _split_name(premise.name)
        if not name_tokens:
            return 0.0
        name_hits = sum(1 for tok in name_tokens if tok in query_tokens)
        name_match = name_hits / len(name_tokens)

        tag_hits = len(_tag_tokens(premise) & query_tokens)
        tag_bonus = min(self._TAG_BONUS_CAP, self._TAG_BONUS_STEP * max(0, tag_hits - name_hits))

        cos = max(0.0, cosine_similarity(query_vector, premise_vector))
        return self._NAME_WEIGHT * name_match + tag_bonus + self._COSINE_WEIGHT * cos

    # ---- public API ---------------------------------------------------------

    def retrieve_premises(self, goal_state: str, k: int = 8) -> list[Premise]:
        self._load_if_needed()
        if not self._premises:
            return []
        query_tokens = _extract_query_tokens(goal_state)
        query_vector = self._embedder.encode([goal_state])[0]
        ranked: list[Premise] = []
        for premise, vector in zip(self._premises, self._premise_vectors, strict=True):
            score = self._score(premise, query_tokens, query_vector, vector)
            if score <= 0.0:
                continue
            ranked.append(
                Premise(
                    name=premise.name,
                    score=round(score, 6),
                    statement=premise.statement,
                    docstring=premise.docstring,
                    file_path=premise.file_path,
                    tags=premise.tags,
                    dependencies=premise.dependencies,
                )
            )
        ranked.sort(key=lambda p: p.score, reverse=True)
        return ranked[: max(1, int(k))]


_DEFAULT_RAG: MathlibRAG | None = None
_DEFAULT_RAG_LOCK = Lock()


def _default_rag() -> MathlibRAG:
    global _DEFAULT_RAG
    if _DEFAULT_RAG is None:
        with _DEFAULT_RAG_LOCK:
            if _DEFAULT_RAG is None:
                from src.planner.retrieval import get_default_embedder
                _DEFAULT_RAG = MathlibRAG(embedder=get_default_embedder())
    return _DEFAULT_RAG


def retrieve_premises(goal_state: str, k: int = 8) -> list[Premise]:
    """Module-level convenience wrapper used by the prover harness."""
    return _default_rag().retrieve_premises(goal_state, k=k)


__all__ = ["MathlibRAG", "Premise", "retrieve_premises"]
