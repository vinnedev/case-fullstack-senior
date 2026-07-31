import time
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, suppress
from threading import Event, Lock, Thread
from typing import Any

from shared.config.settings import get_settings
from shared.db.pool import DictConnection
from shared.logging.wide_event import WideEvent, start_event
from shared.observability.worker_metrics import (
    job_processing_duration_seconds,
    job_queue_wait_seconds,
    jobs_processed_total,
)

SERVICE = "worker"
MAX_ATTEMPTS = 3
LEASE_TIMEOUT_S = 30.0
HEARTBEAT_INTERVAL_S = LEASE_TIMEOUT_S / 3
WORKER_ID = str(uuid.uuid4())
SYSTEM_ACTOR = "system:worker"
CANCELLATIONS_CHANNEL = "jobs_cancelled"
CANCELLATION_MONITOR_STARTUP_TIMEOUT_S = 5.0
CANCELLATION_MONITOR_STOP_TIMEOUT_S = 1.0
type ConnectionFactory = Callable[[], AbstractContextManager[DictConnection]]


class JobCancelledError(Exception):
    pass


class CancellationMonitorSetupError(RuntimeError):
    pass


class CancellationMonitorTimeoutError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelledError


def _cancellation_arrived(conn: DictConnection, job_id: int, timeout: float) -> bool:
    # O canal é compartilhado por todos os jobs: só o payload identifica qual
    # job foi cancelado. Ignorar o payload cancelaria o job errado.
    for notify in conn.notifies(timeout=timeout):
        if notify.payload == str(job_id):
            return True
    return False


def _release_listen(conn: DictConnection) -> None:
    # A assinatura é de sessão e sobrevive à devolução ao pool: sem UNLISTEN,
    # uma conexão reciclada entregaria notificações velhas ao monitor de outro job.
    with suppress(Exception):
        conn.execute("UNLISTEN *")
        conn.commit()
        for _ in conn.notifies(timeout=0):
            pass


@contextmanager
def cancellation_monitor(
    job: dict[str, Any],
    factory: ConnectionFactory | None,
    interval_s: float = 0.1,
    startup_timeout_s: float | None = None,
    stop_timeout_s: float | None = None,
) -> Generator[CancellationToken, None, None]:
    token = CancellationToken()
    if factory is None:
        yield token
        return

    startup_timeout_s = CANCELLATION_MONITOR_STARTUP_TIMEOUT_S if startup_timeout_s is None else startup_timeout_s
    stop_timeout_s = CANCELLATION_MONITOR_STOP_TIMEOUT_S if stop_timeout_s is None else stop_timeout_s
    if startup_timeout_s <= 0 or stop_timeout_s <= 0:
        raise ValueError("os timeouts do monitor de cancelamento devem ser positivos")

    stopped = Event()
    ready = Event()
    setup_succeeded = Event()
    state_lock = Lock()
    monitor_connection: list[DictConnection | None] = [None]

    def record_monitor_error(exc: Exception) -> None:
        with state_lock:
            job.setdefault("cancellation_monitor_error", exc)
        token.cancel()

    def close_monitor_connection() -> None:
        with state_lock:
            monitor_conn = monitor_connection[0]
        if monitor_conn is not None:
            with suppress(Exception):
                monitor_conn.close()

    def monitor() -> None:
        try:
            with factory() as monitor_conn:
                with state_lock:
                    monitor_connection[0] = monitor_conn
                try:
                    monitor_conn.execute(f"LISTEN {CANCELLATIONS_CHANNEL}")
                    monitor_conn.commit()
                    # O SELECT posterior ao LISTEN cobre um cancelamento entre a abertura
                    # da conexão e a ativação efetiva da assinatura.
                    row = monitor_conn.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()
                    monitor_conn.commit()
                    setup_succeeded.set()
                    if row is not None and row["status"] == "cancelled":
                        token.cancel()
                    ready.set()
                    if token.is_cancelled():
                        return
                    while not stopped.is_set():
                        if _cancellation_arrived(monitor_conn, job["id"], timeout=interval_s):
                            token.cancel()
                            return
                finally:
                    _release_listen(monitor_conn)
        except Exception as exc:
            record_monitor_error(exc)
            WideEvent(SERVICE, "cancellation_monitor_error", job_id=job["id"]).error(exc).emit()
        finally:
            with state_lock:
                monitor_connection[0] = None
            ready.set()

    thread = Thread(target=monitor, name=f"job-{job['id']}-cancellation", daemon=True)
    thread.start()

    def stop_monitor() -> None:
        stopped.set()
        thread.join(stop_timeout_s)
        if not thread.is_alive():
            return
        close_monitor_connection()
        thread.join(stop_timeout_s)
        if thread.is_alive():
            exc = CancellationMonitorTimeoutError(f"monitor de cancelamento do job {job['id']} não encerrou dentro do timeout")
            record_monitor_error(exc)
            WideEvent(SERVICE, "cancellation_monitor_error", job_id=job["id"]).error(exc).emit()

    if not ready.wait(startup_timeout_s):
        exc = CancellationMonitorTimeoutError(f"monitor de cancelamento do job {job['id']} não ficou pronto dentro do timeout")
        record_monitor_error(exc)
        stop_monitor()
        raise CancellationMonitorSetupError(
            f"monitor de cancelamento do job {job['id']} não iniciou: {type(exc).__name__}: {exc}"
        ) from exc

    monitor_error: Exception | None = job.get("cancellation_monitor_error")
    if not setup_succeeded.is_set():
        stop_monitor()
        if monitor_error is not None:
            raise CancellationMonitorSetupError(
                f"monitor de cancelamento do job {job['id']} não iniciou: {type(monitor_error).__name__}: {monitor_error}"
            ) from monitor_error
        raise CancellationMonitorSetupError(f"monitor de cancelamento do job {job['id']} encerrou antes de ficar pronto")

    try:
        yield token
    finally:
        stop_monitor()


class LeaseLostError(Exception):
    pass


def _renew_lease(factory: ConnectionFactory, job: dict[str, Any]) -> None:
    with factory() as heartbeat_conn, heartbeat_conn.transaction():
        renewed = heartbeat_conn.execute(
            """
            UPDATE jobs SET locked_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING id
            """,
            (job["id"], job["worker_id"]),
        ).fetchone()
        if renewed is None:
            raise LeaseLostError(f"lease do job {job['id']} não pertence mais a este worker")


HEARTBEAT_MAX_FAILURES = 3


@contextmanager
def lease_heartbeat(
    job: dict[str, Any],
    factory: ConnectionFactory | None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> Generator[None, None, None]:
    if factory is None:
        yield
        return

    stopped = Event()
    errors: list[Exception] = []
    token: CancellationToken | None = job.get("cancel_token")

    def give_up(exc: Exception) -> None:
        # Sem lease garantida outro worker pode reivindicar o job; abortar o
        # trabalho local no próximo checkpoint evita esforço descartado.
        errors.append(exc)
        job["heartbeat_error"] = exc
        if token is not None:
            token.cancel()

    def heartbeat_loop() -> None:
        transient_failures = 0
        while not stopped.wait(interval_s):
            try:
                _renew_lease(factory, job)
                transient_failures = 0
            except LeaseLostError as exc:
                give_up(exc)
                return
            except Exception as exc:
                transient_failures += 1
                if transient_failures >= HEARTBEAT_MAX_FAILURES:
                    give_up(exc)
                    return

    heartbeat = Thread(target=heartbeat_loop, name=f"job-{job['id']}-heartbeat", daemon=True)
    heartbeat.start()
    body_succeeded = False
    try:
        yield
        body_succeeded = True
    finally:
        stopped.set()
        heartbeat.join()
    if body_succeeded and errors:
        raise RuntimeError(f"heartbeat do job {job['id']} falhou") from errors[0]


def seconds_until_next_attempt(conn: DictConnection) -> float | None:
    """Segundos até o backoff mais próximo vencer; None se não há job agendado.

    O NOTIFY do retry chega antes do backoff vencer e nada notifica no
    vencimento — sem este valor, o job esperaria o polling de fallback inteiro.
    """
    row = conn.execute(
        """
        SELECT extract(epoch FROM min(next_attempt_at) - now()) AS s
        FROM jobs
        WHERE status = 'queued' AND next_attempt_at > now()
        """
    ).fetchone()
    if row is None or row["s"] is None:
        return None
    return max(float(row["s"]), 0.0)


def recover_stale_jobs(conn: DictConnection, lease_timeout_s: float = LEASE_TIMEOUT_S) -> int:
    """Transforma claims abandonados em falhas recuperáveis ou DLQ."""
    error = "WorkerLeaseExpired: worker parou antes de finalizar o job"
    with conn.transaction():
        stale = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                last_error = %s,
                next_attempt_at = NULL,
                locked_at = NULL,
                worker_id = NULL,
                updated_at = now()
            WHERE status = 'running'
              AND (
                locked_at IS NULL
                OR locked_at < now() - make_interval(secs => %s)
              )
            RETURNING id, company_id, kind, trace_id, attempts
            """,
            (error, lease_timeout_s),
        ).fetchall()
        for job in stale:
            _record_audit_event(conn, job, "failed")
            if job["attempts"] >= MAX_ATTEMPTS:
                _send_to_dlq(conn, job, error)
    return len(stale)


def _claim_next(conn: DictConnection) -> dict[str, Any] | None:
    with conn.transaction():
        job = conn.execute(
            """
            SELECT id, company_id, kind, trace_id, attempts,
                   extract(epoch FROM now() - updated_at) AS queue_wait_s
            FROM jobs
            WHERE status = 'queued'
              AND attempts < %s
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
            ORDER BY id LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (MAX_ATTEMPTS,),
        ).fetchone()
        if job is None:
            return None
        job_queue_wait_seconds.observe(max(float(job["queue_wait_s"] or 0), 0.0))
        row = conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_at = now(),
                worker_id = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING attempts, locked_at, worker_id
            """,
            (WORKER_ID, job["id"]),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"job {job['id']} sumiu durante o claim")
        job.update(row)
        return job


def _finalize(conn: DictConnection, job: dict[str, Any]) -> str:
    with conn.transaction():
        done = conn.execute(
            """
            UPDATE jobs
            SET status = 'done', locked_at = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING id
            """,
            (job["id"], job["worker_id"]),
        ).fetchone()
        if done is None:
            return "cancelled"
        inserted = conn.execute(
            "INSERT INTO job_results (job_id, payload) VALUES (%s, %s) ON CONFLICT (job_id) DO NOTHING",
            (job["id"], f"resultado sensível da empresa {job['company_id']}"),
        ).rowcount
        _record_audit_event(conn, job, "completed")
        if inserted:
            # a linha da company é o ponto quente do tenant: todos os finalizes
            # serializam nela, então o lock precisa ser o último antes do commit
            conn.execute("UPDATE companies SET job_quota = job_quota - 1 WHERE id = %s", (job["company_id"],))
        return "done"


def _acknowledge_cancellation(conn: DictConnection, job: dict[str, Any]) -> bool:
    # O cancel da API já limpa worker_id/locked_at, então o reconhecimento é
    # pelo status real do job; o UPDATE abaixo só cobre lease residual.
    with conn.transaction():
        row = conn.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()
        conn.execute(
            """
            UPDATE jobs
            SET locked_at = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND status = 'cancelled' AND worker_id = %s
            """,
            (job["id"], job["worker_id"]),
        )
    return row is not None and row["status"] == "cancelled"


def _send_to_dlq(conn: DictConnection, job: dict[str, Any], error: str) -> None:
    conn.execute(
        """
        INSERT INTO dead_letter_jobs (job_id, company_id, kind, attempts, last_error, trace_id, job_created_at)
        SELECT id, company_id, kind, attempts, %s, trace_id, created_at FROM jobs WHERE id = %s
        ON CONFLICT (job_id) WHERE job_id IS NOT NULL DO NOTHING
        """,
        (error, job["id"]),
    )


def _record_audit_event(conn: DictConnection, job: dict[str, Any], event_type: str) -> None:
    conn.execute(
        """
        INSERT INTO job_audit_events (job_id, company_id, event_type, actor, trace_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (job["id"], job["company_id"], event_type, SYSTEM_ACTOR, job["trace_id"]),
    )


def _record_failure(conn: DictConnection, job: dict[str, Any], exc: Exception) -> str:
    error = f"{type(exc).__name__}: {exc}"
    exhausted = job["attempts"] >= MAX_ATTEMPTS
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                last_error = %s,
                next_attempt_at = NULL,
                locked_at = NULL,
                worker_id = NULL,
                updated_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING status
            """,
            (error, job["id"], job["worker_id"]),
        ).fetchone()
        if row is None:
            return "cancelled"
        _record_audit_event(conn, job, "failed")
        if exhausted:
            _send_to_dlq(conn, job, error)
    return row["status"]


def _execute(conn: DictConnection, job: dict[str, Any]) -> None:
    token = job["cancel_token"]
    work_s = get_settings().job_simulated_work_s
    if work_s > 0:
        for _ in range(10):
            token.raise_if_cancelled()
            time.sleep(work_s / 10)
    token.raise_if_cancelled()
    # Transação própria: sem ela o SELECT abriria uma transação implícita e o
    # finalize rodaria como savepoint, adiando o commit real para o teardown.
    with conn.transaction():
        company = conn.execute("SELECT id FROM companies WHERE id = %s", (job["company_id"],)).fetchone()
    if company is None:
        raise RuntimeError(f"company {job['company_id']} não encontrada para o job {job['id']}")


def process_next(
    conn: DictConnection,
    heartbeat_factory: ConnectionFactory | None = None,
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
) -> bool:
    recover_stale_jobs(conn)
    job = _claim_next(conn)
    if job is None:
        return False
    started = time.perf_counter()
    outcome = "failed"
    event = start_event(
        SERVICE,
        "job_processed",
        job_id=job["id"],
        company_id=job["company_id"],
        job_kind=job["kind"],
        trace_id=job["trace_id"],
        attempts=job["attempts"],
    )
    try:
        with cancellation_monitor(job, heartbeat_factory) as cancel_token:
            job["cancel_token"] = cancel_token
            cancel_token.raise_if_cancelled()
            with lease_heartbeat(job, heartbeat_factory, heartbeat_interval_s):
                _execute(conn, job)
        cancel_token.raise_if_cancelled()
        outcome = _finalize(conn, job)
        event.add(outcome=outcome)
    except JobCancelledError:
        conn.rollback()
        if _acknowledge_cancellation(conn, job):
            outcome = "cancelled"
            event.add(outcome=outcome)
        else:
            # Token disparado sem cancelamento real (ex.: lease perdida pelo
            # heartbeat): registrar como falha em vez de deixar o job órfão.
            cause = (
                job.get("heartbeat_error")
                or job.get("cancellation_monitor_error")
                or RuntimeError("cancelamento sinalizado sem job cancelado")
            )
            status = _record_failure(conn, job, cause)
            event.error(cause, outcome="failed", job_status=status)
    except Exception as exc:
        conn.rollback()
        status = _record_failure(conn, job, exc)
        event.error(exc, outcome="failed", job_status=status)
        if status == "failed" and job["attempts"] >= MAX_ATTEMPTS:
            event.add(dead_lettered=True)
    finally:
        jobs_processed_total.labels(outcome=outcome).inc()
        job_processing_duration_seconds.labels(outcome=outcome).observe(time.perf_counter() - started)
        event.emit()
    return True
