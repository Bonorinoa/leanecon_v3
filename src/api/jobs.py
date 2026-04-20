"""SQLite-backed job store with stage telemetry, audit logs, and SSE subscribers."""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import DB_PATH, JOB_TTL_SECONDS
from src.observability.models import AuditEvent, StageTiming, TokenUsage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    status: str
    created_at: str
    updated_at: str
    review_state: str | None
    result: dict[str, Any] | None
    error: str | None


class JobStore:
    def __init__(self, db_path: Path = DB_PATH, ttl_seconds: int = JOB_TTL_SECONDS) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self.initialize()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    review_state TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_stage_usage (
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    estimated_cost_usd REAL,
                    latency_ms REAL,
                    success INTEGER NOT NULL,
                    usage_source TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, stage)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_timing (
                    job_id TEXT PRIMARY KEY,
                    planner_ms REAL NOT NULL,
                    formalizer_ms REAL NOT NULL,
                    prover_ms REAL NOT NULL,
                    total_ms REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    prompt_hash TEXT,
                    response_hash TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute("SELECT id, created_at FROM jobs").fetchall()
            expired: list[str] = []
            for job_id, created_at in rows:
                try:
                    created_ts = datetime.fromisoformat(created_at).timestamp()
                except ValueError:
                    continue
                if created_ts < cutoff:
                    expired.append(str(job_id))
            for job_id in expired:
                connection.execute("DELETE FROM job_stage_usage WHERE job_id = ?", (job_id,))
                connection.execute("DELETE FROM job_timing WHERE job_id = ?", (job_id,))
                connection.execute("DELETE FROM job_audit_events WHERE job_id = ?", (job_id,))
                connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                self._subscribers.pop(job_id, None)
            connection.commit()

    def create(self, *, status: str, review_state: str | None, result: dict[str, Any] | None = None) -> JobRecord:
        job = JobRecord(
            id=str(uuid.uuid4()),
            status=status,
            review_state=review_state,
            created_at=_utc_now(),
            updated_at=_utc_now(),
            result=result,
            error=None,
        )
        self._upsert(job)
        return self._hydrate_job(job)

    def _upsert(self, job: JobRecord) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO jobs (id, status, review_state, created_at, updated_at, result_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    review_state = excluded.review_state,
                    updated_at = excluded.updated_at,
                    result_json = excluded.result_json,
                    error = excluded.error
                """,
                (
                    job.id,
                    job.status,
                    job.review_state,
                    job.created_at,
                    job.updated_at,
                    json.dumps(job.result) if job.result is not None else None,
                    job.error,
                ),
            )
            connection.commit()

    def _fetch_job(self, job_id: str) -> JobRecord | None:
        self._cleanup_expired()
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT id, status, review_state, created_at, updated_at, result_json, error FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return JobRecord(
            id=str(row[0]),
            status=str(row[1]),
            review_state=str(row[2]) if row[2] is not None else None,
            created_at=str(row[3]),
            updated_at=str(row[4]),
            result=json.loads(str(row[5])) if row[5] else None,
            error=str(row[6]) if row[6] else None,
        )

    def _hydrate_result(self, job_id: str, result: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = dict(result) if isinstance(result, dict) else ({} if result is not None else None)
        if payload is None:
            return None
        payload["usage_by_stage"] = self.stage_usage(job_id)
        payload["timing_breakdown"] = self.timing_breakdown(job_id)
        payload["audit_summary"] = self.audit_summary(job_id)
        return payload

    def _hydrate_job(self, job: JobRecord) -> JobRecord:
        return replace(job, result=self._hydrate_result(job.id, job.result))

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        review_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> JobRecord | None:
        job = self._fetch_job(job_id)
        if job is None:
            return None
        if status is not None:
            job.status = status
        if review_state is not None:
            job.review_state = review_state
        if result is not None:
            job.result = result
        if error is not None:
            job.error = error
        job.updated_at = _utc_now()
        self._upsert(job)
        hydrated = self._hydrate_job(job)
        self.publish(
            job_id,
            {
                "status": hydrated.status,
                "review_state": hydrated.review_state,
                "result": hydrated.result,
                "error": hydrated.error,
            },
        )
        return hydrated

    def get(self, job_id: str) -> JobRecord | None:
        job = self._fetch_job(job_id)
        if job is None:
            return None
        return self._hydrate_job(job)

    def counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        return {str(status): int(count) for status, count in rows}

    def record_stage_usage(self, job_id: str, usage: TokenUsage) -> None:
        created_at = _utc_now()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO job_stage_usage (
                    job_id, stage, provider, model, input_tokens, output_tokens, estimated_cost_usd,
                    latency_ms, success, usage_source, error_code, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, stage) DO UPDATE SET
                    provider = excluded.provider,
                    model = excluded.model,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    estimated_cost_usd = excluded.estimated_cost_usd,
                    latency_ms = excluded.latency_ms,
                    success = excluded.success,
                    usage_source = excluded.usage_source,
                    error_code = excluded.error_code,
                    created_at = excluded.created_at
                """,
                (
                    job_id,
                    usage.stage,
                    usage.provider,
                    usage.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.estimated_cost_usd,
                    usage.latency_ms,
                    1 if usage.success else 0,
                    usage.usage_source,
                    usage.error_code,
                    created_at,
                ),
            )
            connection.commit()
        self._publish_snapshot(job_id)

    def record_timing(self, job_id: str, timing: StageTiming) -> None:
        updated_at = _utc_now()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO job_timing (job_id, planner_ms, formalizer_ms, prover_ms, total_ms, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    planner_ms = excluded.planner_ms,
                    formalizer_ms = excluded.formalizer_ms,
                    prover_ms = excluded.prover_ms,
                    total_ms = excluded.total_ms,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    timing.planner_ms,
                    timing.formalizer_ms,
                    timing.prover_ms,
                    timing.total_ms,
                    updated_at,
                ),
            )
            connection.commit()
        self._publish_snapshot(job_id)

    def record_audit_event(self, job_id: str, event: AuditEvent) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO job_audit_events (
                    job_id, stage, event_type, provider, model, success, error_code, error_message,
                    prompt_hash, response_hash, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    event.stage,
                    event.event_type,
                    event.provider,
                    event.model,
                    1 if event.success else 0,
                    event.error_code,
                    event.error_message,
                    event.prompt_hash,
                    event.response_hash,
                    json.dumps(event.metadata, sort_keys=True),
                    _utc_now(),
                ),
            )
            connection.commit()
        self._publish_snapshot(job_id)

    def stage_usage(self, job_id: str) -> dict[str, dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT stage, provider, model, input_tokens, output_tokens, estimated_cost_usd,
                       latency_ms, success, usage_source, error_code, created_at
                FROM job_stage_usage
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        payload: dict[str, dict[str, Any]] = {}
        for row in rows:
            stage = str(row[0])
            payload[stage] = {
                "stage": stage,
                "provider": str(row[1]),
                "model": str(row[2]),
                "input_tokens": int(row[3]) if row[3] is not None else None,
                "output_tokens": int(row[4]) if row[4] is not None else None,
                "estimated_cost_usd": round(float(row[5]), 8) if row[5] is not None else None,
                "latency_ms": round(float(row[6]), 3) if row[6] is not None else None,
                "success": bool(row[7]),
                "usage_source": str(row[8]),
                "error_code": str(row[9]) if row[9] is not None else None,
                "created_at": str(row[10]),
            }
        return payload

    def timing_breakdown(self, job_id: str) -> dict[str, float] | None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT planner_ms, formalizer_ms, prover_ms, total_ms
                FROM job_timing
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "planner_ms": round(float(row[0]), 3),
            "formalizer_ms": round(float(row[1]), 3),
            "prover_ms": round(float(row[2]), 3),
            "total_ms": round(float(row[3]), 3),
        }

    def audit_summary(self, job_id: str, *, limit: int = 20) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT stage, event_type, provider, model, success, error_code, error_message,
                       prompt_hash, response_hash, metadata_json, created_at
                FROM job_audit_events
                WHERE job_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        failure_counts: dict[str, int] = {}
        for row in rows:
            error_code = str(row[5]) if row[5] is not None else None
            if error_code:
                failure_counts[error_code] = failure_counts.get(error_code, 0) + 1
            events.append(
                {
                    "stage": str(row[0]),
                    "event_type": str(row[1]),
                    "provider": str(row[2]),
                    "model": str(row[3]),
                    "success": bool(row[4]),
                    "error_code": error_code,
                    "error_message": str(row[6]) if row[6] is not None else None,
                    "prompt_hash": str(row[7]) if row[7] is not None else None,
                    "response_hash": str(row[8]) if row[8] is not None else None,
                    "metadata": json.loads(str(row[9])) if row[9] else {},
                    "created_at": str(row[10]),
                }
            )
        latest_event = events[0] if events else None
        return {
            "event_count": len(events),
            "latest_event": latest_event,
            "failure_counts": failure_counts,
            "events": list(reversed(events)),
        }

    def recent_prover_stats(self, *, limit: int = 100) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                SELECT job_id, success, created_at
                FROM job_audit_events
                WHERE stage = 'prover' AND event_type = 'job_terminal'
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        total = len(rows)
        if total == 0:
            return {
                "window": limit,
                "jobs": 0,
                "success_rate": None,
                "avg_cost_per_successful_job": None,
            }
        successful_job_ids = [str(row[0]) for row in rows if bool(row[1])]
        cost_rows: list[tuple[object, ...]] = []
        if successful_job_ids:
            placeholders = ",".join("?" for _ in successful_job_ids)
            with closing(sqlite3.connect(self.db_path)) as connection:
                cost_rows = connection.execute(
                    f"""
                    SELECT job_id, SUM(COALESCE(estimated_cost_usd, 0))
                    FROM job_stage_usage
                    WHERE job_id IN ({placeholders})
                    GROUP BY job_id
                    """,
                    successful_job_ids,
                ).fetchall()
        costs = [float(row[1]) for row in cost_rows if row[1] is not None]
        return {
            "window": limit,
            "jobs": total,
            "success_rate": round(sum(1 for row in rows if bool(row[1])) / total, 6),
            "avg_cost_per_successful_job": round(sum(costs) / len(costs), 8) if costs else None,
        }

    def metrics_snapshot(self) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            usage_rows = connection.execute(
                """
                SELECT stage, provider, model, input_tokens, output_tokens, estimated_cost_usd, success, error_code
                FROM job_stage_usage
                """
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT stage, event_type, success, error_code
                FROM job_audit_events
                """
            ).fetchall()
        usage_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "records": len(usage_rows),
        }
        usage_by_stage: dict[str, dict[str, Any]] = {}
        usage_by_model: dict[str, dict[str, Any]] = {}
        for row in usage_rows:
            stage = str(row[0])
            provider = str(row[1])
            model = str(row[2])
            input_tokens = int(row[3]) if row[3] is not None else 0
            output_tokens = int(row[4]) if row[4] is not None else 0
            estimated_cost = float(row[5]) if row[5] is not None else 0.0
            success = bool(row[6])
            error_code = str(row[7]) if row[7] is not None else None

            usage_totals["input_tokens"] += input_tokens
            usage_totals["output_tokens"] += output_tokens
            usage_totals["estimated_cost_usd"] += estimated_cost

            stage_bucket = usage_by_stage.setdefault(
                stage,
                {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "records": 0, "failures": 0},
            )
            stage_bucket["input_tokens"] += input_tokens
            stage_bucket["output_tokens"] += output_tokens
            stage_bucket["estimated_cost_usd"] += estimated_cost
            stage_bucket["records"] += 1
            if not success:
                stage_bucket["failures"] += 1
                if error_code:
                    stage_bucket.setdefault("failure_counts", {})
                    stage_bucket["failure_counts"][error_code] = stage_bucket["failure_counts"].get(error_code, 0) + 1

            model_key = f"{provider}:{model}"
            model_bucket = usage_by_model.setdefault(
                model_key,
                {
                    "provider": provider,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "records": 0,
                },
            )
            model_bucket["input_tokens"] += input_tokens
            model_bucket["output_tokens"] += output_tokens
            model_bucket["estimated_cost_usd"] += estimated_cost
            model_bucket["records"] += 1

        failure_counts: dict[str, int] = {}
        for row in audit_rows:
            if row[3] is None:
                continue
            code = str(row[3])
            failure_counts[code] = failure_counts.get(code, 0) + 1

        usage_totals["estimated_cost_usd"] = round(float(usage_totals["estimated_cost_usd"]), 8)
        for bucket in usage_by_stage.values():
            bucket["estimated_cost_usd"] = round(float(bucket["estimated_cost_usd"]), 8)
        for bucket in usage_by_model.values():
            bucket["estimated_cost_usd"] = round(float(bucket["estimated_cost_usd"]), 8)

        return {
            "usage_totals": usage_totals,
            "usage_by_stage": usage_by_stage,
            "usage_by_model": usage_by_model,
            "failure_counts": failure_counts,
            "recent": self.recent_prover_stats(),
        }

    def _publish_snapshot(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        self.publish(
            job_id,
            {
                "status": job.status,
                "review_state": job.review_state,
                "result": job.result,
                "error": job.error,
            },
        )

    def publish(self, job_id: str, payload: dict[str, Any]) -> None:
        for queue in self._subscribers.get(job_id, []):
            queue.put_nowait(payload)

    async def subscribe(self, job_id: str):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(job_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[job_id].remove(queue)


job_store = JobStore()
