"""Retrieval and LSP-context helpers for Prover.

This module owns local Mathlib RAG, LeanSearch retrieval, enrichment, and
progress-derived second-pass retrieval decisions.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

from src.formalizer.models import FormalizationPacket
from src.observability import (
    AuditEvent,
    LeanLSPUnavailableError,
    LeanSearchFailureEvent,
    ProgressDelta,
    RetrievalEvent,
    stable_hash_text,
)
from src.prover.lsp_cache import LSPCache
from src.prover.models import ProverTraceStep
from src.prover.tactics import theorem_goal_statement

if TYPE_CHECKING:
    from src.prover.execution import _ActiveProofSession


def _compat_log_event(*args: Any, **kwargs: Any) -> Any:
    from src.prover import prover as prover_module

    return prover_module.log_event(*args, **kwargs)

def _contains_lsp_unavailable(value: Any) -> bool:
    if isinstance(value, str):
        return "lsp_unavailable" in value
    if isinstance(value, dict):
        return any(_contains_lsp_unavailable(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_lsp_unavailable(child) for child in value)
    return False

_MATHLIB_IDENT_RE = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)*)\b")

_MATHLIB_IDENT_STOPWORDS = frozenset(
    {"True", "False", "None", "Type", "Prop", "Sort", "Set", "Nat", "Int", "Real"}
)

def _extract_mathlib_idents(text: str) -> list[str]:
    """Return Mathlib-style CamelCase identifiers in *text*, in first-seen order.

    Used to build refined leansearch queries from goal/hypothesis context.
    Stopwords filter out common non-discriminating types so queries stay
    targeted on lemma-bearing identifiers like ``IsCompact``/``Monotone``.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _MATHLIB_IDENT_RE.finditer(text):
        ident = match.group(1)
        if ident in _MATHLIB_IDENT_STOPWORDS:
            continue
        # Require at least one lowercase letter to skip pure-acronym noise like "BBB".
        if ident not in seen:
            seen[ident] = None
    return list(seen.keys())

_UNKNOWN_IDENT_RE = re.compile(
    r"unknown\s+identifier\s+[`'\"]([A-Za-z_][A-Za-z0-9_.']*)[`'\"]",
    re.IGNORECASE,
)

def _extract_unknown_identifier(error_text: str) -> str | None:
    if not error_text:
        return None
    match = _UNKNOWN_IDENT_RE.search(error_text)
    return match.group(1) if match else None

def _query_from_failed_identifier(ident: str) -> str:
    """Split a snake_case/CamelCase identifier into a LeanSearch query."""
    if not ident:
        return ""
    parts = [p for p in ident.replace(".", "_").split("_") if p]
    if not parts:
        return ""
    joined = " ".join(parts)
    if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
        joined = f"{joined} theorem"
    return joined[:200]

class ProverRetrievalMixin:

    """Mixin extracted from the legacy Prover monolith."""

    def _mathlib_harness_state(
        self,
        *,
        session: _ActiveProofSession,
        goals: list[str],
    ) -> dict[str, Any]:
        code = session.read_code()
        proof_path = session.proof_path
        diagnostics: Any = None
        code_actions: Any = None
        file_outline: Any = None
        if proof_path is not None:
            proof_line = self._active_proof_line(code)
            try:
                diagnostics = self.lsp_client.lean_diagnostic_messages(
                    proof_path,
                    severity="error",
                    start_line=max(1, proof_line - 2),
                    end_line=proof_line + 2,
                )
            except LeanLSPUnavailableError as exc:
                diagnostics = {"error": f"lsp_unavailable: {exc}"}
            try:
                code_actions = self.lsp_client.lean_code_actions(proof_path, line=proof_line)
            except LeanLSPUnavailableError as exc:
                code_actions = {"error": f"lsp_unavailable: {exc}"}
            try:
                file_outline = self.lsp_client.lean_file_outline(proof_path, max_declarations=40)
            except (AttributeError, LeanLSPUnavailableError) as exc:
                file_outline = {"error": f"lsp_unavailable: {exc}"}
        code_hash = stable_hash_text(code)
        state_hash = stable_hash_text(
            json.dumps(
                {
                    "code_hash": code_hash,
                    "goals": goals,
                    "diagnostics": diagnostics,
                },
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            )
        )
        return {
            "code": code,
            "code_hash": code_hash,
            "goals": list(goals),
            "diagnostics": diagnostics,
            "code_actions": code_actions,
            "file_outline": file_outline,
            "state_hash": state_hash,
        }

    def _retrieve_mathlib_premises(
        self,
        goals: list[str],
        *,
        k: int,
        claim_id: str | None = None,
    ) -> RetrievalEvent:
        started_at = time.perf_counter()
        premises: list[dict[str, Any]] = []
        error_code: str | None = None
        try:
            from src.retrieval.mathlib_rag import retrieve_premises

            raw_premises = retrieve_premises("\n".join(goals), k=k)
        except Exception as exc:  # Stage 2 H.2: surface RAG failures as audit events.
            self._handle_lsp_error(
                "mathlib_rag", exc, context="\n".join(goals)[:120]
            )
            error_code = "mathlib_rag_unavailable"
            raw_premises = []
        for premise in list(raw_premises or [])[:k]:
            if hasattr(premise, "to_dict"):
                payload = premise.to_dict()
            elif hasattr(premise, "__dict__"):
                payload = dict(premise.__dict__)
            elif isinstance(premise, dict):
                payload = dict(premise)
            else:
                payload = {"name": str(premise)}
            premises.append(payload)
        scores = []
        for premise in premises:
            try:
                scores.append(float(premise.get("score", 0.0)))
            except (TypeError, ValueError):
                scores.append(0.0)
        return RetrievalEvent(
            retrieved_premises=premises,
            scores=scores,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            k=k,
            claim_id=claim_id,
            error_code=error_code,
        )

    def _retrieve_lean_search_premises(
        self,
        query: str,
        *,
        k: int,
        retrieval_pass: int = 1,
        state: dict[str, Any] | None = None,
        claim_id: str | None = None,
    ) -> RetrievalEvent:
        """Enhanced with observable LeanSearchFailureEvent on 0-results or exceptions,
        plus one retry using refined subgoal query from state (backwards-compatible).
        Preserves exact success path, budget recording, and enrichment.
        """
        started_at = time.perf_counter()
        original_query = query
        used_query = query
        refined_query: str | None = None
        retry_attempted = False
        premises: list[dict[str, Any]] = []
        error: Exception | None = None

        if not self.budget_tracker.can_search():
            error = RuntimeError("search budget exhausted")
        else:
            for attempt in range(2):  # exactly one retry
                if attempt > 0:
                    retry_attempted = True
                    if state is not None:
                        refined_query = self._refined_leansearch_query(state)
                        if refined_query and refined_query != used_query:
                            used_query = refined_query
                            premises = []  # reset for retry
                    else:
                        # fallback refinement from query text (subgoal-like)
                        refined_query = (
                            self._refined_leansearch_query({"goals": [used_query]})
                            or used_query[:240]
                        )
                        if refined_query != used_query:
                            used_query = refined_query
                            premises = []

                try:
                    payload = self.lsp_client.lean_leansearch(
                        used_query, num_results=k
                    )
                    # Record budget only on a successful round-trip so that retries on
                    # LSP outage do not exhaust the search budget for the run.
                    self.budget_tracker.record("lean_leansearch")
                    items = (payload or {}).get("items") or []
                    for item in items[:k]:
                        name = str(
                            item.get("name") or item.get("theorem_name") or ""
                        )
                        if not name:
                            continue
                        premises.append(
                            {
                                "name": name,
                                "score": 0.80,
                                "statement": item.get("type")
                                or item.get("statement"),
                                "docstring": item.get("docstring"),
                                "file_path": item.get("module"),
                                "tags": [],
                                "dependencies": [],
                                "source": "lean_leansearch",
                            }
                        )
                    if premises:
                        break  # success
                    # successful call but empty = failure for retry
                    if attempt == 0:
                        continue
                except Exception as exc:
                    error = exc
                    self._handle_lsp_error(
                        "lean_leansearch", exc, context=used_query[:120]
                    )
                    if attempt == 0:
                        continue
                    break

        # Make failures observable with structured event (visible in JSONL/audit)
        if not premises or error is not None:
            failure_event = LeanSearchFailureEvent(
                query=original_query,
                refined_query=refined_query,
                error_code=(
                    "no_results"
                    if not premises and error is None
                    else "lsp_error"
                ),
                error_message=str(error) if error else "lean_leansearch returned 0 results",
                retry_attempted=retry_attempted,
                hit=bool(premises),
                latency_ms=(time.perf_counter() - started_at) * 1000.0,
                retrieval_pass=retrieval_pass,
                claim_id=claim_id,
            )
            backend = getattr(self, "primary_backend", None)
            _compat_log_event(
                AuditEvent(
                    stage="prover",
                    event_type="LeanSearchFailureEvent",
                    provider=backend.provider if backend is not None else "unknown",
                    model=backend.model if backend is not None else "unknown",
                    success=False,
                    error_code=failure_event.error_code,
                    error_message=failure_event.error_message,
                    metadata=failure_event.to_dict(),
                )
            )

        # Sprint 23 Task 2: enrich each leansearch premise with outline+hover.
        enriched_count = self._enrich_leansearch_premises(premises)
        scores = [float(p.get("score", 0.0)) for p in premises]
        return RetrievalEvent(
            retrieved_premises=premises,
            scores=scores,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            k=k,
            source="lean_leansearch",
            query=used_query,
            enriched_count=enriched_count,
            retrieval_pass=retrieval_pass,
            claim_id=claim_id,
        )

    def _get_lsp_cache(self) -> LSPCache:
        """Return the per-prove LSP cache, rebinding if ``lsp_client`` changed.

        Tests may monkey-patch ``self.lsp_client`` after the cache was first
        constructed; we detect that and refresh the cache so it points at the
        active client. This costs O(1) and only allocates when the client
        identity changes.
        """
        cache = self._lsp_cache
        if cache is None or cache.lsp_client is not self.lsp_client:
            cache = LSPCache(
                self.lsp_client,
                on_error=lambda tool, exc, ctx: self._handle_lsp_error(
                    tool, exc, context=ctx
                ),
            )
            self._lsp_cache = cache
        return cache

    def _enrich_leansearch_premises(self, premises: list[dict[str, Any]]) -> int:
        return self._get_lsp_cache().enrich_premises(premises)

    @staticmethod
    def _merge_retrieval_premises(
        local: list[dict[str, Any]],
        remote: list[dict[str, Any]],
        k: int,
    ) -> list[dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for p in local:
            name = p.get("name", "")
            if name and (name not in by_name or p.get("score", 0.0) > by_name[name].get("score", 0.0)):
                by_name[name] = p
        for p in remote:
            name = p.get("name", "")
            if name and (name not in by_name or p.get("score", 0.0) > by_name[name].get("score", 0.0)):
                by_name[name] = p
        merged = sorted(by_name.values(), key=lambda p: float(p.get("score", 0.0)), reverse=True)
        return merged[:k]

    @staticmethod
    def _should_do_second_retrieval(
        *,
        last_delta: ProgressDelta | None,
        budget_remaining_frac: float,
        turn: int | None = None,  # accepted for backwards compatibility, ignored
    ) -> bool:
        """Stall-recovery heuristic for a second leansearch pass.

        Stage 2-followup C: dropped the strict ``turn == 1`` gate. Fires on
        any turn where the previous turn produced no progress and budget is
        ample. Per-target idempotence is enforced at the call site via the
        ``_second_retrieval_targets`` set, so this stays a pure heuristic.
        """
        del turn  # Backwards-compat parameter, no longer load-bearing.
        if last_delta is None:
            return False
        if last_delta.goals_reduced:
            return False
        return budget_remaining_frac > 0.30

    @staticmethod
    def _refined_leansearch_query(state: dict[str, Any]) -> str | None:
        """Build a refined leansearch query from the current unsolved subgoal text.

        Stage 2 P1.A: walks the full goal text (hypotheses + ⊢) and pulls
        Mathlib-style CamelCase identifiers (IsCompact, Monotone, BddAbove,
        IsMaxOn, ContinuousOn, Tendsto, …). These dominate Mathlib lemma names
        so a query built from them retrieves better than raw goal text.
        Falls back to the previous goal-line behaviour when no identifiers
        are detected.
        """
        goals = state.get("goals") or []
        if not goals:
            return None
        first = goals[0] if isinstance(goals, list) else goals
        text = str(first or "").strip()
        if not text:
            return None
        idents = _extract_mathlib_idents(text)
        if idents:
            joined = " ".join(idents[:4])
            if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
                joined = f"{joined} theorem"
            return joined[:200]
        # Fallback: original goal-only behaviour for non-Mathlib goals.
        first_line = text.splitlines()[0].strip()
        if not first_line:
            first_line = text
        key_part = first_line.split("⊢")[-1].strip() if "⊢" in first_line else first_line
        if not any(kw in key_part.lower() for kw in ["theorem", "lemma", "prove"]):
            key_part += " theorem"
        return key_part[:200]

    @staticmethod
    def _rescue_query_from_recent_trace(
        trace: list[ProverTraceStep],
        target_name: str | None,
    ) -> str | None:
        """Stage 2-followup D: scan the recent harness trace for an
        ``unknown identifier '<X>'`` error from a prior tactic on this target,
        and turn it into a concept-token query for LeanSearch.
        """
        if not trace:
            return None
        for step in reversed(trace):
            if target_name and step.target_name and step.target_name != target_name:
                continue
            if step.action_type != "mathlib_native_harness_loop":
                continue
            if not step.tool_result:
                continue
            ident = _extract_unknown_identifier(step.tool_result)
            if ident:
                query = _query_from_failed_identifier(ident)
                return query or None
        return None

    def _last_progress_delta_obj(self) -> ProgressDelta | None:
        """Return the most recent ProgressDelta as a typed object (None if empty)."""
        if not self._progress_deltas:
            return None
        last = self._progress_deltas[-1]
        try:
            return ProgressDelta(
                goals_reduced=bool(last.get("goals_reduced", False)),
                complexity_reduced=bool(last.get("complexity_reduced", False)),
                stall_detected=bool(last.get("stall_detected", False)),
                goal_count_before=int(last.get("goal_count_before", 0)),
                goal_count_after=int(last.get("goal_count_after", 0)),
                complexity_before=int(last.get("complexity_before", 0)),
                complexity_after=int(last.get("complexity_after", 0)),
            )
        except (TypeError, ValueError):
            return None

    def _progress_delta_from_states(
        self,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
    ) -> ProgressDelta:
        before_goals = list(before_state.get("goals") or [])
        after_goals = list(after_state.get("goals") or [])
        before_complexity = sum(len(str(goal).strip()) for goal in before_goals)
        after_complexity = sum(len(str(goal).strip()) for goal in after_goals)
        return ProgressDelta(
            goals_reduced=len(after_goals) < len(before_goals),
            complexity_reduced=after_complexity < before_complexity,
            stall_detected=(
                before_goals == after_goals
                and (
                    before_state.get("state_hash") == after_state.get("state_hash")
                    or after_complexity >= before_complexity
                )
            ),
            goal_count_before=len(before_goals),
            goal_count_after=len(after_goals),
            complexity_before=before_complexity,
            complexity_after=after_complexity,
        )

    def _mathlib_native_search_query(
        self,
        *,
        packet: FormalizationPacket,
        goals: list[str],
        current_code: str,
    ) -> str:
        theorem_goal = theorem_goal_statement(current_code) or ""
        # Stage 2-followup B: prefer Mathlib-style CamelCase identifiers when
        # we can find them in the goal/theorem text. Falls back to the original
        # verbose join when no idents are present, preserving existing behaviour
        # on natural-language-only claims.
        ident_source = "\n".join(
            chunk for chunk in (theorem_goal, *(goals[:1])) if chunk
        )
        idents = _extract_mathlib_idents(ident_source)
        if idents:
            joined = " ".join(idents[:5])
            if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
                joined = f"{joined} theorem"
            return joined[:200]
        chunks = [packet.claim, theorem_goal, *(goals[:1])]
        return " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())[:900]
