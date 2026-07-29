from typing import Any

from shared.db.pool import DictConnection


class AdminService:
    def __init__(self, conn: DictConnection) -> None:
        self._conn = conn

    def list_all_jobs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._conn.execute(
            "SELECT id, company_id, status FROM jobs ORDER BY id LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()

    def count_all_jobs(self) -> int:
        row = self._conn.execute("SELECT count(*) AS n FROM jobs").fetchone()
        return row["n"] if row else 0

    def count_dead_letters(self) -> int:
        row = self._conn.execute("SELECT count(*) AS n FROM dead_letter_jobs").fetchone()
        return row["n"] if row else 0

    def list_dead_letters(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._conn.execute(
            """
            SELECT id, job_id, company_id, kind, attempts, last_error, trace_id, job_created_at, failed_at
            FROM dead_letter_jobs
            ORDER BY failed_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
