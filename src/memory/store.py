"""SQLite-backed episodic memory store."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from src.config import MEMORY_DB_PATH
from src.memory.models import ProofTrace, ProofTraceSchema

logger = logging.getLogger(__name__)


class ProofTraceStore:
    def __init__(self, db_path: Path = MEMORY_DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            columns_sql = ", ".join(f"{name} {column_type}" for name, column_type in ProofTraceSchema)
            with closing(sqlite3.connect(self.db_path)) as connection:
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS proof_traces (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        {columns_sql}
                    )
                    """
                )
                connection.commit()
            self._initialized = True

    def record(self, trace: ProofTrace) -> None:
        self.initialize()
        with self._lock:
            with closing(sqlite3.connect(self.db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO proof_traces (
                        claim_id, claim_text, preamble_names_json, tactic_sequence_json,
                        stage_outcomes_json, failure_class, repair_count, outcome,
                        formalizer_model, timestamp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace.claim_id,
                        trace.claim_text,
                        json.dumps(trace.preamble_names),
                        json.dumps(trace.tactic_sequence),
                        json.dumps(trace.stage_outcomes),
                        trace.failure_class,
                        trace.repair_count,
                        trace.outcome,
                        trace.formalizer_model,
                        trace.timestamp,
                    ),
                )
                connection.commit()

    def _rows_to_traces(self, rows: list[tuple[object, ...]]) -> list[ProofTrace]:
        traces: list[ProofTrace] = []
        for row in rows:
            traces.append(
                ProofTrace(
                    claim_id=str(row[0]),
                    claim_text=str(row[1]),
                    preamble_names=list(json.loads(str(row[2]))),
                    tactic_sequence=list(json.loads(str(row[3]))),
                    stage_outcomes=dict(json.loads(str(row[4]))),
                    failure_class=str(row[5]) if row[5] else None,
                    repair_count=int(row[6]),
                    outcome=str(row[7]),
                    formalizer_model=str(row[8]),
                    timestamp=str(row[9]),
                )
            )
        return traces

    def query_similar(
        self,
        preamble_names: list[str],
        limit: int = 3,
        *,
        outcome: str | None = None,
    ) -> list[ProofTrace]:
        self.initialize()
        if not preamble_names:
            return []
        like_terms = [f'%"{name}"%' for name in preamble_names]
        score_expr = " + ".join("CASE WHEN preamble_names_json LIKE ? THEN 1 ELSE 0 END" for _ in like_terms)
        filters: list[str] = [f"({score_expr}) > 0"]
        params: list[object] = [*like_terms]
        if outcome is not None:
            filters.append("outcome = ?")
            params.append(outcome)
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    claim_id, claim_text, preamble_names_json, tactic_sequence_json,
                    stage_outcomes_json, failure_class, repair_count, outcome,
                    formalizer_model, timestamp
                FROM proof_traces
                WHERE {" AND ".join(filters)}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return self._rows_to_traces(rows)

    def list_recent(self, *, limit: int = 10, outcome: str | None = None) -> list[ProofTrace]:
        self.initialize()
        filters: list[str] = []
        params: list[object] = []
        if outcome is not None:
            filters.append("outcome = ?")
            params.append(outcome)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    claim_id, claim_text, preamble_names_json, tactic_sequence_json,
                    stage_outcomes_json, failure_class, repair_count, outcome,
                    formalizer_model, timestamp
                FROM proof_traces
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return self._rows_to_traces(rows)

    def counts(self) -> dict[str, int]:
        self.initialize()
        with closing(sqlite3.connect(self.db_path)) as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM proof_traces").fetchone()[0])
            verified = int(
                connection.execute("SELECT COUNT(*) FROM proof_traces WHERE outcome = 'verified'").fetchone()[0]
            )
        return {"total": total, "verified": verified, "failed": max(total - verified, 0)}


trace_store = ProofTraceStore()
