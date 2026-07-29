import json
from threading import Event, Thread

import pytest

from modules.jobs import processor
from modules.jobs.processor import process_next
from shared.db.pool import connect_dict


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
        other.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (1,))
        other.commit()

    assert stopped.wait(2)
    worker.join()
    job = job_row(db)
    assert job["status"] == "cancelled"
    assert db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"] == 0
    assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10


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
