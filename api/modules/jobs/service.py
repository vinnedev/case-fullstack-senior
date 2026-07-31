from typing import Any

from modules.jobs.errors import (
    CompanyUnknownError,
    ConcurrencyLimitError,
    IdempotencyKeyConflictError,
    InvalidJobStateError,
    JobNotFoundError,
    ResultNotFoundError,
    RetryLimitError,
)
from shared.db.pool import DictConnection

MAX_ATTEMPTS = 3
JOBS_CHANNEL = "jobs_queued"
CANCELLATIONS_CHANNEL = "jobs_cancelled"


def _require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("query deveria ter retornado uma linha")
    return row


class JobsService:
    def __init__(self, conn: DictConnection) -> None:
        self._conn = conn

    def list_jobs(
        self,
        company_id: int,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._conn.execute(
            """
            SELECT j.id, j.kind, j.status, j.created_at,
                   (SELECT count(*) FROM job_results r WHERE r.job_id = j.id) AS result_count
            FROM jobs j
            WHERE j.company_id = %(company_id)s
              AND (%(status)s::text IS NULL OR j.status = %(status)s)
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"company_id": company_id, "status": status, "limit": limit, "offset": offset},
        ).fetchall()

    def count_jobs(self, company_id: int, status: str | None = None) -> int:
        return _require_row(
            self._conn.execute(
                """
                SELECT count(*) AS n FROM jobs
                WHERE company_id = %(company_id)s
                  AND (%(status)s::text IS NULL OR status = %(status)s)
                """,
                {"company_id": company_id, "status": status},
            ).fetchone()
        )["n"]

    def get_job(self, company_id: int, job_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT id, company_id, kind, status, attempts, last_error
            FROM jobs
            WHERE id = %s AND company_id = %s
            """,
            (job_id, company_id),
        ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        row = dict(row)
        events = self._get_audit_events(company_id, job_id)
        cancellation_event = next((event for event in events if event["event_type"] == "cancelled"), None)
        row["cancellation"] = (
            {"cancelled_by": cancellation_event["actor"], "cancelled_at": cancellation_event["occurred_at"]}
            if cancellation_event is not None
            else None
        )
        row["audit_events"] = events
        return row

    def _get_audit_events(self, company_id: int, job_id: int) -> list[dict[str, Any]]:
        return self._conn.execute(
            """
            SELECT event_type, actor, occurred_at, trace_id
            FROM job_audit_events
            WHERE job_id = %s AND company_id = %s
            ORDER BY occurred_at ASC, id ASC
            """,
            (job_id, company_id),
        ).fetchall()

    def _record_audit_event(
        self,
        job_id: int,
        company_id: int,
        event_type: str,
        actor: str,
        trace_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO job_audit_events (job_id, company_id, event_type, actor, trace_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (job_id, company_id, event_type, actor, trace_id),
        )

    def get_result(self, company_id: int, job_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT r.payload
            FROM job_results r
            JOIN jobs j ON j.id = r.job_id
            WHERE r.job_id = %s AND j.company_id = %s
            """,
            (job_id, company_id),
        ).fetchone()
        if row is None:
            raise ResultNotFoundError(job_id)
        return row

    def create_job(
        self,
        company_id: int,
        kind: str,
        trace_id: str | None,
        idempotency_key: str,
        submitted_by: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        existing = self._find_by_idempotency_key(company_id, idempotency_key)
        if existing is not None:
            return self._replay(existing, kind), False
        company = self._conn.execute(
            "SELECT max_concurrent_jobs FROM companies WHERE id = %s FOR UPDATE",
            (company_id,),
        ).fetchone()
        if company is None:
            raise CompanyUnknownError(company_id)
        # A primeira leitura é um fast path. Enquanto esta request aguardava o
        # lock da empresa, outra pode ter criado exatamente a mesma chave.
        # Revalidar sob o lock evita devolver 429 no replay que ocupou a última
        # vaga do limite de concorrência.
        existing = self._find_by_idempotency_key(company_id, idempotency_key)
        if existing is not None:
            return self._replay(existing, kind), False
        running = _require_row(
            self._conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE company_id = %s AND status IN ('queued', 'running')",
                (company_id,),
            ).fetchone()
        )["n"]
        if running >= company["max_concurrent_jobs"]:
            raise ConcurrencyLimitError(limit=company["max_concurrent_jobs"], running=running)
        job = self._conn.execute(
            """
            INSERT INTO jobs (company_id, kind, status, trace_id, idempotency_key)
            VALUES (%s, %s, 'queued', %s, %s)
            ON CONFLICT (company_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING id, status
            """,
            (company_id, kind, trace_id, idempotency_key),
        ).fetchone()
        if job is None:
            # corrida entre duas requests com a mesma chave: a outra venceu, devolve o job dela
            winner = _require_row(self._find_by_idempotency_key(company_id, idempotency_key))
            return self._replay(winner, kind), False
        if submitted_by is not None:
            self._record_audit_event(job["id"], company_id, "submitted", submitted_by, trace_id)
        self._notify_queued(job["id"])
        return job, True

    @staticmethod
    def _replay(existing: dict[str, Any], kind: str) -> dict[str, Any]:
        if existing["kind"] != kind:
            raise IdempotencyKeyConflictError(existing["kind"])
        return {"id": existing["id"], "status": existing["status"]}

    def _find_by_idempotency_key(self, company_id: int, idempotency_key: str) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT id, status, kind FROM jobs WHERE company_id = %s AND idempotency_key = %s",
            (company_id, idempotency_key),
        ).fetchone()

    def cancel_job(self, company_id: int, job_id: int, cancelled_by: str, trace_id: str | None = None) -> dict[str, Any]:
        row = self._conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', locked_at = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND company_id = %s AND status IN ('queued', 'running')
            RETURNING id, status
            """,
            (job_id, company_id),
        ).fetchone()
        if row is not None:
            self._record_audit_event(job_id, company_id, "cancelled", cancelled_by, trace_id)
            self._conn.execute("SELECT pg_notify(%s, %s)", (CANCELLATIONS_CHANNEL, str(job_id)))
            return row
        current = self.get_job(company_id, job_id)
        raise InvalidJobStateError(current["status"])

    def _notify_queued(self, job_id: int) -> None:
        # LISTEN/NOTIFY nativo: acorda o worker no commit, sem polling agressivo.
        # A fila durável continua sendo a tabela jobs — o NOTIFY é só o sinal.
        self._conn.execute("SELECT pg_notify(%s, %s)", (JOBS_CHANNEL, str(job_id)))

    def retry_job(self, company_id: int, job_id: int, requested_by: str, trace_id: str | None = None) -> dict[str, Any]:
        row = self._conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                last_error = NULL,
                next_attempt_at = now() + make_interval(
                    secs => 5 * power(2, greatest(attempts, 1) - 1)
                ),
                locked_at = NULL,
                worker_id = NULL,
                -- jobs legados (seed) nascem sem trace; o retry os torna rastreáveis
                trace_id = COALESCE(trace_id, %s),
                updated_at = now()
            WHERE id = %s AND company_id = %s AND status = 'failed' AND attempts < %s
            RETURNING id, status, attempts
            """,
            (trace_id, job_id, company_id, MAX_ATTEMPTS),
        ).fetchone()
        if row is not None:
            self._record_audit_event(job_id, company_id, "retry_requested", requested_by, trace_id)
            self._notify_queued(row["id"])
            return row
        current = self.get_job(company_id, job_id)
        if current["status"] == "failed":
            raise RetryLimitError(attempts=current["attempts"], limit=MAX_ATTEMPTS)
        raise InvalidJobStateError(current["status"])
