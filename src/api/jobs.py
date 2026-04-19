"""SQLite-backed job store with in-process SSE subscribers."""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import DB_PATH, JOB_TTL_SECONDS


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
            connection.commit()

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute("SELECT id, created_at FROM jobs").fetchall()
            expired = []
            for job_id, created_at in rows:
                try:
                    created_ts = datetime.fromisoformat(created_at).timestamp()
                except ValueError:
                    continue
                if created_ts < cutoff:
                    expired.append(job_id)
            for job_id in expired:
                connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                self._subscribers.pop(str(job_id), None)
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
        return job

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

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        review_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> JobRecord | None:
        job = self.get(job_id)
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
        self.publish(job_id, {"status": job.status, "review_state": job.review_state, "result": job.result, "error": job.error})
        return job

    def get(self, job_id: str) -> JobRecord | None:
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

    def counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        return {str(status): int(count) for status, count in rows}

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
