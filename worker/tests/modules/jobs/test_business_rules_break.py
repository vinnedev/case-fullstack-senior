"""Bateria adversarial do worker: quebra das regras de processamento.

Regras sob ataque (fontes: TASKS.md, docs/worker.md, DECISIONS.md):
  W1  Só processa jobs 'queued' cujo backoff venceu
  W2  Nunca produz resultado duplicado nem debita quota duas vezes
  W3  Cancelamento vence o finalize (resultado descartado, quota intacta)
  W4  Máximo de 3 tentativas; ao esgotar → 'failed' + DLQ auditável
  W5  Backoff exponencial com jitter, sempre dentro da faixa esperada
  W6  Falha nunca deixa o job em estado inconsistente ou perdido
"""

import pytest

from modules.jobs import processor
from modules.jobs.processor import process_next
from shared.db.pool import connect_dict


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(processor.time, "sleep", lambda _: None)


def boom(_conn, _job):
    raise RuntimeError("falha adversarial")


def seed_company(db, quota=10):
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', %s)", (quota,))


def add_job(db, job_id=1, status="queued", attempts=0, next_attempt_at=None, trace_id=None):
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts, next_attempt_at, trace_id)
        VALUES (%s, 1, 'report', %s, %s, %s, %s)
        """,
        (job_id, status, attempts, next_attempt_at, trace_id),
    )
    db.commit()


class TestW1ClaimRules:
    @pytest.mark.parametrize("status", ["running", "done", "failed", "cancelled"])
    def test_never_claims_non_queued_jobs(self, db, status):
        seed_company(db)
        add_job(db, status=status)
        assert process_next(db) is False

    def test_never_claims_job_with_pending_backoff(self, db):
        seed_company(db)
        db.execute(
            "INSERT INTO jobs (id, company_id, kind, status, next_attempt_at) VALUES (1, 1, 'report', 'queued', now() + interval '1 hour')"
        )
        db.commit()
        assert process_next(db) is False
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "queued"

    def test_empty_queue_is_not_an_error(self, db):
        seed_company(db)
        assert process_next(db) is False
        assert process_next(db) is False


class TestW2NoDoubleWork:
    def test_preexisting_result_is_not_duplicated_and_quota_untouched(self, db):
        seed_company(db, quota=10)
        add_job(db)
        db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'anterior')")
        db.commit()
        assert process_next(db) is True
        assert db.execute("SELECT count(*) AS n FROM job_results WHERE job_id = 1").fetchone()["n"] == 1
        assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10

    def test_quota_debited_exactly_once_per_job(self, db):
        seed_company(db, quota=5)
        add_job(db)
        assert process_next(db) is True
        assert process_next(db) is False  # nada mais a fazer
        assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 4

    def test_negative_quota_does_not_block_processing(self, db):
        # regra de negócio atual: quota é contador, não trava (documentado em DECISIONS §4)
        seed_company(db, quota=0)
        add_job(db)
        assert process_next(db) is True
        assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == -1


class TestW3CancelWins:
    def test_cancel_during_work_discards_result_and_quota(self, db, test_database, monkeypatch):
        seed_company(db, quota=10)
        add_job(db)

        def cancel_midway(_conn, job):
            with connect_dict(test_database) as other:
                other.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (job["id"],))
                other.commit()

        monkeypatch.setattr(processor, "_execute", cancel_midway)
        assert process_next(db) is True
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "cancelled"
        assert db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"] == 0
        assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 10

    def test_cancel_during_failure_does_not_resurrect_job(self, db, test_database, monkeypatch):
        seed_company(db)
        add_job(db)

        def cancel_then_fail(_conn, job):
            with connect_dict(test_database) as other:
                other.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (job["id"],))
                other.commit()
            raise RuntimeError("falhou depois do cancel")

        monkeypatch.setattr(processor, "_execute", cancel_then_fail)
        assert process_next(db) is True
        # cancelado permanece cancelado: falha não devolve para a fila
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "cancelled"
        assert db.execute("SELECT count(*) AS n FROM dead_letter_jobs").fetchone()["n"] == 0


class TestW4MaxAttemptsAndDLQ:
    def test_exhausting_attempts_sends_to_dlq_once(self, db, monkeypatch):
        seed_company(db)
        add_job(db, attempts=2, trace_id="trace-adv")
        monkeypatch.setattr(processor, "_execute", boom)
        assert process_next(db) is True
        job = db.execute("SELECT status, attempts FROM jobs WHERE id = 1").fetchone()
        assert job == {"status": "failed", "attempts": 3}
        dlq = db.execute("SELECT * FROM dead_letter_jobs").fetchall()
        assert len(dlq) == 1
        assert dlq[0]["trace_id"] == "trace-adv"
        assert dlq[0]["attempts"] == 3
        # job em 'failed' não é reivindicado de novo → nenhuma segunda entrada na DLQ
        assert process_next(db) is False
        assert db.execute("SELECT count(*) AS n FROM dead_letter_jobs").fetchone()["n"] == 1

    def test_dlq_survives_job_deletion(self, db, monkeypatch):
        seed_company(db)
        add_job(db, attempts=2)
        monkeypatch.setattr(processor, "_execute", boom)
        process_next(db)
        db.execute("DELETE FROM job_results WHERE job_id = 1")
        db.execute("DELETE FROM jobs WHERE id = 1")
        db.commit()
        row = db.execute("SELECT job_id, company_id, kind, attempts, last_error FROM dead_letter_jobs").fetchone()
        assert row["job_id"] is None  # FK vira NULL
        assert row["company_id"] == 1 and row["kind"] == "report" and row["attempts"] == 3
        assert "falha adversarial" in row["last_error"]

    def test_job_below_limit_waits_failed_for_manual_retry(self, db, monkeypatch):
        seed_company(db)
        add_job(db, attempts=0)
        monkeypatch.setattr(processor, "_execute", boom)
        assert process_next(db) is True
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "failed"
        assert db.execute("SELECT count(*) AS n FROM dead_letter_jobs").fetchone()["n"] == 0


def test_failure_does_not_schedule_itself(db, monkeypatch):
    seed_company(db)
    add_job(db)
    monkeypatch.setattr(processor, "_execute", boom)
    process_next(db)
    row = db.execute("SELECT status, next_attempt_at FROM jobs WHERE id = 1").fetchone()
    assert row == {"status": "failed", "next_attempt_at": None}
    # sem retry manual, o worker não reivindica a falha novamente
    assert process_next(db) is False


class TestW6NoInconsistentState:
    def test_failed_job_keeps_error_and_no_partial_result(self, db, monkeypatch):
        seed_company(db, quota=7)
        add_job(db)
        monkeypatch.setattr(processor, "_execute", boom)
        process_next(db)
        job = db.execute("SELECT status, attempts, last_error FROM jobs WHERE id = 1").fetchone()
        assert job["attempts"] == 1
        assert "RuntimeError" in job["last_error"]
        assert db.execute("SELECT count(*) AS n FROM job_results").fetchone()["n"] == 0
        assert db.execute("SELECT job_quota FROM companies WHERE id = 1").fetchone()["job_quota"] == 7

    def test_successful_retry_after_failures_clears_error(self, db, monkeypatch):
        seed_company(db)
        add_job(db)
        monkeypatch.setattr(processor, "_execute", boom)
        process_next(db)
        # simula o retry manual da API: limpa backoff e erro
        db.execute("UPDATE jobs SET status = 'queued', last_error = NULL, next_attempt_at = NULL WHERE id = 1")
        db.commit()
        monkeypatch.undo()
        monkeypatch.setattr(processor.time, "sleep", lambda _: None)
        assert process_next(db) is True
        job = db.execute("SELECT status, attempts, last_error FROM jobs WHERE id = 1").fetchone()
        assert job["status"] == "done"
        assert job["attempts"] == 2
        assert job["last_error"] is None
