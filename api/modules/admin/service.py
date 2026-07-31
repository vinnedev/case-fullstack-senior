from typing import Any

from shared.db.pool import DictConnection


class AdminService:
    def __init__(self, conn: DictConnection) -> None:
        self._conn = conn

    def list_companies(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        # Pagina as empresas ANTES de agregar: agregar a tabela jobs inteira e
        # só então aplicar LIMIT repetiria a causa raiz do sintoma 1.
        return self._conn.execute(
            """
            SELECT c.id, c.name, c.max_concurrent_jobs, c.job_quota,
                   count(j.id) AS total_jobs,
                   count(j.id) FILTER (WHERE j.status = 'queued') AS queued,
                   count(j.id) FILTER (WHERE j.status = 'running') AS running,
                   count(j.id) FILTER (WHERE j.status = 'done') AS done,
                   count(j.id) FILTER (WHERE j.status = 'failed') AS failed,
                   count(j.id) FILTER (WHERE j.status = 'cancelled') AS cancelled
            FROM (
                SELECT id, name, max_concurrent_jobs, job_quota
                FROM companies
                ORDER BY id
                LIMIT %(limit)s OFFSET %(offset)s
            ) c
            LEFT JOIN jobs j ON j.company_id = c.id
            GROUP BY c.id, c.name, c.max_concurrent_jobs, c.job_quota
            ORDER BY c.id
            """,
            {"limit": limit, "offset": offset},
        ).fetchall()

    def count_companies(self) -> int:
        row = self._conn.execute("SELECT count(*) AS n FROM companies").fetchone()
        return row["n"] if row else 0

    def list_all_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        company_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        # Contrato do case original: colunas e ordenação por id preservadas.
        return self._conn.execute(
            """
            SELECT id, company_id, status
            FROM jobs
            WHERE (%(company_id)s::bigint IS NULL OR company_id = %(company_id)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
            ORDER BY id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"company_id": company_id, "status": status, "limit": limit, "offset": offset},
        ).fetchall()

    def count_all_jobs(self, company_id: int | None = None, status: str | None = None) -> int:
        row = self._conn.execute(
            """
            SELECT count(*) AS n FROM jobs
            WHERE (%(company_id)s::bigint IS NULL OR company_id = %(company_id)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
            """,
            {"company_id": company_id, "status": status},
        ).fetchone()
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
