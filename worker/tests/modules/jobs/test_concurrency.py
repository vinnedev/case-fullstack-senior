from concurrent.futures import ThreadPoolExecutor

import pytest

from modules.jobs import processor
from modules.jobs.processor import process_next
from shared.db.pool import connect_dict


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(processor.time, "sleep", lambda _: None)


def test_two_workers_never_process_the_same_job(db, test_database):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (1, 1, 'report', 'queued')")
    db.commit()

    def run_worker() -> bool:
        with connect_dict(test_database) as conn:
            processed = process_next(conn)
            conn.commit()
            return processed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_worker(), range(2)))

    # SKIP LOCKED: exatamente um worker pega o job; o outro vê fila vazia
    assert sorted(results) == [False, True]
    job = db.execute("SELECT status, attempts FROM jobs WHERE id = 1").fetchone()
    assert job == {"status": "done", "attempts": 1}
    results_count = db.execute("SELECT count(*) AS n FROM job_results WHERE job_id = 1").fetchone()["n"]
    assert results_count == 1
    quota = db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"]
    assert quota == 9


def test_many_workers_drain_queue_without_duplicates(db, test_database):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 100)")
    for i in range(1, 7):
        db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (%s, 1, 'report', 'queued')", (i,))
    db.commit()

    def drain_all() -> int:
        n = 0
        with connect_dict(test_database) as conn:
            while True:
                if not process_next(conn):
                    break
                conn.commit()
                n += 1
        return n

    with ThreadPoolExecutor(max_workers=3) as pool:
        processed = list(pool.map(lambda _: drain_all(), range(3)))

    assert sum(processed) == 6
    done = db.execute("SELECT count(*) AS n FROM jobs WHERE status = 'done'").fetchone()["n"]
    results = db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"]
    quota = db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"]
    assert (done, results, quota) == (6, 6, 94)
