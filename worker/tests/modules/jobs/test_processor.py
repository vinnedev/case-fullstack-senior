import json
from collections.abc import Generator
from contextlib import contextmanager
from threading import Event, Thread
from typing import cast

import pytest

from modules.jobs import processor
from modules.jobs.processor import process_next
from shared.db.pool import DictConnection, connect_dict


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(processor.time, "sleep", lambda _: None)


def seed(db, status="queued", attempts=0, company_id=1, trace_id=None):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10) ON CONFLICT DO NOTHING")
    db.execute(
        "INSERT INTO jobs (id, company_id, kind, status, attempts, trace_id) VALUES (1, %s, 'report', %s, %s, %s)",
        (company_id, status, attempts, trace_id),
    )
    db.commit()


def job_row(db):
    return db.execute("SELECT status, attempts, last_error FROM jobs WHERE id = 1").fetchone()


def test_returns_false_when_queue_is_empty(db):
    assert process_next(db) is False


def test_processes_queued_job_to_done(db):
    seed(db)
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "done" and job["attempts"] == 1
    result = db.execute("SELECT payload FROM job_results WHERE job_id = 1").fetchone()
    assert "empresa 1" in result["payload"]
    assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 9
    audit = db.execute("SELECT event_type, actor FROM job_audit_events WHERE job_id = 1").fetchone()
    assert audit == {"event_type": "completed", "actor": "system:worker"}


def test_ignores_non_queued_jobs(db):
    seed(db, status="done")
    assert process_next(db) is False


def test_never_claims_queued_job_at_attempt_limit(db):
    seed(db, attempts=3)
    assert process_next(db) is False
    assert job_row(db) == {"status": "queued", "attempts": 3, "last_error": None}


def test_failure_waits_for_manual_retry_with_error_recorded(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(processor, "_execute", lambda conn, job: (_ for _ in ()).throw(RuntimeError("falha simulada")))
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "failed" and job["attempts"] == 1
    assert "RuntimeError" in job["last_error"]
    assert db.execute("SELECT event_type FROM job_audit_events WHERE job_id = 1").fetchone() == {"event_type": "failed"}


def test_failure_marks_failed_after_max_attempts(db, monkeypatch):
    seed(db, attempts=2)
    monkeypatch.setattr(processor, "_execute", lambda conn, job: (_ for _ in ()).throw(RuntimeError("boom")))
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "failed" and job["attempts"] == 3


def test_result_and_quota_are_idempotent(db):
    seed(db)
    db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'já existia')")
    db.commit()
    assert process_next(db) is True
    results = db.execute("SELECT count(*) AS n FROM job_results WHERE job_id = 1").fetchone()["n"]
    assert results == 1
    assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10


def test_cancelled_mid_flight_discards_result(db, test_database, monkeypatch):
    import psycopg

    seed(db)

    def cancel_during_work(conn, job):
        with psycopg.connect(test_database) as other:
            other.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (job["id"],))
            other.commit()

    monkeypatch.setattr(processor, "_execute", cancel_during_work)
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "cancelled"
    assert db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"] == 0
    assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10


def test_cancelled_mid_flight_rolls_back_and_stops_current_processing(db, test_database, monkeypatch):
    seed(db)
    started = Event()
    stopped = Event()

    def cancellable_work(conn, job):
        conn.execute("INSERT INTO job_results (job_id, payload) VALUES (%s, %s)", (job["id"], "partial"))
        started.set()
        while not job["cancel_token"].is_cancelled():
            Event().wait(0.01)
        job["cancel_token"].raise_if_cancelled()

    monkeypatch.setattr(processor, "_execute", cancellable_work)

    def run_worker():
        with connect_dict(test_database) as worker_conn:
            process_next(worker_conn, heartbeat_factory=lambda: connect_dict(test_database))
        stopped.set()

    worker = Thread(target=run_worker)
    worker.start()
    assert started.wait(2)

    import psycopg

    with psycopg.connect(test_database) as other:
        other.execute("UPDATE jobs SET status = 'cancelled', worker_id = NULL, locked_at = NULL WHERE id = %s", (1,))
        other.execute("SELECT pg_notify('jobs_cancelled', '1')")
        other.commit()

    assert stopped.wait(2)
    worker.join()
    job = job_row(db)
    assert job["status"] == "cancelled"
    assert db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"] == 0
    assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10


def test_execution_waits_for_cancellation_monitor_readiness(db, test_database, monkeypatch):
    seed(db)
    factory_entered = Event()
    allow_monitor = Event()
    execute_called = Event()
    completed = Event()

    @contextmanager
    def factory():
        factory_entered.set()
        assert allow_monitor.wait(2)
        with connect_dict(test_database) as monitor_conn:
            yield monitor_conn

    def execute(_conn, _job):
        execute_called.set()

    monkeypatch.setattr(processor, "_execute", execute)

    def run_worker():
        process_next(db, heartbeat_factory=factory)
        completed.set()

    worker = Thread(target=run_worker)
    worker.start()
    assert factory_entered.wait(2)
    assert execute_called.is_set() is False
    allow_monitor.set()
    assert completed.wait(2)
    worker.join()
    assert execute_called.is_set() is True
    assert job_row(db)["status"] == "done"


def test_monitor_setup_failure_fails_claim_without_executing_job(db, monkeypatch):
    seed(db)
    execute_called = Event()

    @contextmanager
    def failing_factory():
        raise RuntimeError("monitor connection unavailable")
        yield

    monkeypatch.setattr(processor, "_execute", lambda _conn, _job: execute_called.set())

    assert process_next(db, heartbeat_factory=failing_factory) is True
    job = job_row(db)
    assert execute_called.is_set() is False
    assert job["status"] == "failed"
    assert "CancellationMonitorSetupError" in job["last_error"]
    assert "RuntimeError: monitor connection unavailable" in job["last_error"]


def test_initially_cancelled_job_never_enters_execute(db, test_database, monkeypatch):
    import psycopg

    seed(db)
    execute_called = Event()

    @contextmanager
    def already_cancelled_factory():
        with psycopg.connect(test_database) as canceller:
            canceller.execute("UPDATE jobs SET status = 'cancelled', worker_id = NULL, locked_at = NULL WHERE id = 1")
            canceller.commit()
        with connect_dict(test_database) as monitor_conn:
            yield monitor_conn

    monkeypatch.setattr(processor, "_execute", lambda _conn, _job: execute_called.set())

    assert process_next(db, heartbeat_factory=already_cancelled_factory) is True
    assert execute_called.is_set() is False
    assert job_row(db)["status"] == "cancelled"


def test_monitor_readiness_timeout_fails_claim_and_stops_worker(db, monkeypatch):
    seed(db)
    factory_entered = Event()
    release_factory = Event()
    factory_exited = Event()
    execute_called = Event()
    completed = Event()
    failures = []

    @contextmanager
    def blocked_factory() -> Generator[DictConnection, None, None]:
        factory_entered.set()
        assert release_factory.wait(2)
        factory_exited.set()
        yield cast(DictConnection, None)

    monkeypatch.setattr(processor, "_execute", lambda _conn, _job: execute_called.set())
    monkeypatch.setattr(processor, "CANCELLATION_MONITOR_STARTUP_TIMEOUT_S", 0.01)
    monkeypatch.setattr(processor, "CANCELLATION_MONITOR_STOP_TIMEOUT_S", 0.01)

    def run_worker():
        try:
            process_next(db, heartbeat_factory=blocked_factory)
        except processor.FatalCancellationMonitorError as exc:
            failures.append(exc)
        finally:
            completed.set()

    worker = Thread(target=run_worker)
    worker.start()
    assert factory_entered.wait(2)
    assert completed.wait(2)
    worker.join()
    release_factory.set()
    assert factory_exited.wait(2)
    job = job_row(db)
    assert execute_called.is_set() is False
    assert len(failures) == 1
    assert job["status"] == "failed"
    assert "FatalCancellationMonitorError" in job["last_error"]


def test_monitor_shutdown_timeout_fails_current_job_and_stops_worker(db, test_database, monkeypatch):
    seed(db)
    polling_started = Event()
    release_poll = Event()
    poll_exited = Event()

    def stalled_notify(_conn, _job_id, timeout):
        polling_started.set()
        try:
            assert release_poll.wait(2)
        finally:
            poll_exited.set()
        return False

    monkeypatch.setattr(processor, "_cancellation_arrived", stalled_notify)
    monkeypatch.setattr(processor, "_execute", lambda _conn, _job: polling_started.wait(2))
    try:
        monkeypatch.setattr(processor, "CANCELLATION_MONITOR_STOP_TIMEOUT_S", 0.01)
        with pytest.raises(processor.FatalCancellationMonitorError, match="fechamento forçado"):
            process_next(db, heartbeat_factory=lambda: connect_dict(test_database))
        job = job_row(db)
        assert job["status"] == "failed"
        assert "FatalCancellationMonitorError" in job["last_error"]
    finally:
        release_poll.set()
    assert poll_exited.wait(2)


def test_monitor_runtime_failure_cancels_work_and_records_monitor_cause(db, test_database, monkeypatch):
    seed(db)
    monitor_polling = Event()
    trigger_monitor_failure = Event()
    execute_started = Event()

    def failing_notify(_conn, _job_id, timeout):
        monitor_polling.set()
        assert trigger_monitor_failure.wait(2)
        raise RuntimeError("monitor listener dropped")

    def cancellable_work(_conn, job):
        execute_started.set()
        assert monitor_polling.wait(2)
        trigger_monitor_failure.set()
        while not job["cancel_token"].is_cancelled():
            Event().wait(0.01)
        job["cancel_token"].raise_if_cancelled()

    monkeypatch.setattr(processor, "_cancellation_arrived", failing_notify)
    monkeypatch.setattr(processor, "_execute", cancellable_work)

    assert process_next(db, heartbeat_factory=lambda: connect_dict(test_database)) is True
    job = job_row(db)
    assert execute_started.is_set() is True
    assert job["status"] == "failed"
    assert "RuntimeError: monitor listener dropped" in job["last_error"]


def test_cancel_notify_for_other_job_is_ignored(db, test_database):
    import psycopg

    seed(db)
    job = {"id": 1}
    with processor.cancellation_monitor(job, lambda: connect_dict(test_database), interval_s=0.02) as token:
        Event().wait(0.2)
        with psycopg.connect(test_database) as other:
            other.execute("SELECT pg_notify('jobs_cancelled', '999')")
            other.commit()
        Event().wait(0.3)
        assert token.is_cancelled() is False
        with psycopg.connect(test_database) as other:
            other.execute("UPDATE jobs SET status = 'cancelled' WHERE id = 1")
            other.execute("SELECT pg_notify('jobs_cancelled', '1')")
            other.commit()
        deadline = Event()
        for _ in range(100):
            if token.is_cancelled():
                break
            deadline.wait(0.02)
        assert token.is_cancelled() is True


def test_monitor_unlistens_before_returning_connection(db, test_database):
    seed(db)
    reused = connect_dict(test_database)

    @contextmanager
    def factory():
        yield reused

    with processor.cancellation_monitor({"id": 1}, factory, interval_s=0.02):
        Event().wait(0.1)
    assert reused.execute("SELECT pg_listening_channels()").fetchall() == []
    reused.close()


def test_spurious_cancellation_records_failure(db, monkeypatch):
    seed(db)

    def tripping_work(conn, job):
        job["cancel_token"].cancel()
        job["cancel_token"].raise_if_cancelled()

    monkeypatch.setattr(processor, "_execute", tripping_work)
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "failed"
    assert "cancelamento sinalizado" in job["last_error"]


def test_heartbeat_lease_loss_trips_cancel_token(db, test_database):
    seed(db, status="running")
    token = processor.CancellationToken()
    job = {"id": 1, "worker_id": "outro-worker", "cancel_token": token}
    with pytest.raises(RuntimeError, match="heartbeat"):
        with processor.lease_heartbeat(job, lambda: connect_dict(test_database), interval_s=0.01):
            for _ in range(200):
                if token.is_cancelled():
                    break
                Event().wait(0.01)
            assert token.is_cancelled() is True
    assert isinstance(job["heartbeat_error"], processor.LeaseLostError)


def test_api_style_cancel_is_acknowledged_not_logged_as_failure(db, capsys, monkeypatch):
    seed(db)

    def cancel_like_api(conn, job):
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', worker_id = NULL, locked_at = NULL WHERE id = %s",
            (job["id"],),
        )
        conn.commit()
        job["cancel_token"].cancel()
        job["cancel_token"].raise_if_cancelled()

    monkeypatch.setattr(processor, "_execute", cancel_like_api)
    assert process_next(db) is True
    assert job_row(db)["status"] == "cancelled"
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["outcome"] == "cancelled"
    assert event["level"] == "info"


def test_seconds_until_next_attempt(db):
    assert processor.seconds_until_next_attempt(db) is None
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        "INSERT INTO jobs (id, company_id, kind, status, next_attempt_at) VALUES (1, 1, 'report', 'queued', now() + interval '5 seconds')"
    )
    db.commit()
    pending = processor.seconds_until_next_attempt(db)
    assert pending is not None and 3.0 < pending <= 5.0
    db.execute("UPDATE jobs SET next_attempt_at = now() - interval '1 second' WHERE id = 1")
    db.commit()
    assert processor.seconds_until_next_attempt(db) is None


def test_trace_id_flows_into_processing_event(db, capsys):
    seed(db, trace_id="req-abc")
    process_next(db)
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["trace_id"] == "req-abc"
    assert event["outcome"] == "done"


def test_notify_wakes_listener(db, test_database):
    import psycopg

    from modules.jobs.listener import JOBS_CHANNEL, wait_for_wakeup

    with psycopg.connect(test_database, autocommit=True) as listen_conn:
        listen_conn.execute(f"LISTEN {JOBS_CHANNEL}")
        assert wait_for_wakeup(listen_conn, timeout=0.2) is False
        db.execute("SELECT pg_notify('jobs_queued', '1')")
        db.commit()
        assert wait_for_wakeup(listen_conn, timeout=2.0) is True


def test_failure_does_not_schedule_automatic_retry(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(processor, "_execute", lambda conn, job: (_ for _ in ()).throw(RuntimeError("boom")))
    assert process_next(db) is True
    row = db.execute("SELECT status, next_attempt_at FROM jobs WHERE id = 1").fetchone()
    assert row == {"status": "failed", "next_attempt_at": None}


def test_backoff_job_is_not_claimed_before_due(db):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        "INSERT INTO jobs (id, company_id, kind, status, next_attempt_at) VALUES (1, 1, 'report', 'queued', now() + interval '60 seconds')"
    )
    db.commit()
    assert process_next(db) is False


def test_backoff_job_is_claimed_after_due(db):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        "INSERT INTO jobs (id, company_id, kind, status, next_attempt_at) VALUES (1, 1, 'report', 'queued', now() - interval '1 second')"
    )
    db.commit()
    assert process_next(db) is True
    assert job_row(db)["status"] == "done"


def test_exhausted_job_goes_to_dlq_with_full_audit_trail(db, monkeypatch):
    seed(db, attempts=2, trace_id="trace-dlq")
    monkeypatch.setattr(processor, "_execute", lambda conn, job: (_ for _ in ()).throw(RuntimeError("boom final")))
    assert process_next(db) is True
    job = job_row(db)
    assert job["status"] == "failed" and job["attempts"] == 3
    dlq = db.execute("SELECT * FROM dead_letter_jobs").fetchall()
    assert len(dlq) == 1
    entry = dlq[0]
    assert entry["job_id"] == 1
    assert entry["company_id"] == 1
    assert entry["kind"] == "report"
    assert entry["attempts"] == 3
    assert "boom final" in entry["last_error"]
    assert entry["trace_id"] == "trace-dlq"
    assert entry["failed_at"] is not None


def test_stale_running_job_is_recovered_as_failed(db):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts, locked_at, worker_id)
        VALUES (1, 1, 'report', 'running', 1, now() - interval '5 minutes', 'dead-worker')
        """
    )
    db.commit()

    assert process_next(db) is False
    row = db.execute("SELECT status, locked_at, worker_id, last_error FROM jobs WHERE id = 1").fetchone()
    assert row["status"] == "failed"
    assert row["locked_at"] is None and row["worker_id"] is None
    assert "WorkerLeaseExpired" in row["last_error"]


def test_running_job_without_lease_is_recovered_as_failed(db):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute("INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES (1, 1, 'report', 'running', 1)")
    db.commit()

    assert processor.recover_stale_jobs(db) == 1
    assert job_row(db)["status"] == "failed"


def test_heartbeat_prevents_active_job_from_being_recovered(db, test_database, monkeypatch):
    from threading import Event

    from shared.db.pool import connect_dict

    seed(db)

    def slow_work(_conn, _job):
        Event().wait(0.08)
        with connect_dict(test_database) as observer:
            assert processor.recover_stale_jobs(observer, lease_timeout_s=0.03) == 0

    monkeypatch.setattr(processor, "_execute", slow_work)
    assert process_next(
        db,
        heartbeat_factory=lambda: connect_dict(test_database),
        heartbeat_interval_s=0.01,
    )
    assert job_row(db)["status"] == "done"


def test_stale_third_attempt_is_recovered_to_dlq_once(db):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts, locked_at, worker_id, trace_id)
        VALUES (1, 1, 'report', 'running', 3, now() - interval '5 minutes', 'dead-worker', 'lease-trace')
        """
    )
    db.commit()

    assert process_next(db) is False
    assert process_next(db) is False
    entry = db.execute("SELECT job_id, attempts, trace_id, last_error FROM dead_letter_jobs").fetchone()
    assert entry["job_id"] == 1 and entry["attempts"] == 3
    assert entry["trace_id"] == "lease-trace"
    assert "WorkerLeaseExpired" in entry["last_error"]
    assert db.execute("SELECT count(*) AS n FROM dead_letter_jobs").fetchone()["n"] == 1
