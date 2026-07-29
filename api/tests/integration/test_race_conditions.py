from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

USER = {"X-Auth": "1:user"}


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20)")
    db.commit()


class TestRaceConditions:
    def test_concurrent_creates_never_exceed_limit(self, client, seeded, db):
        with ThreadPoolExecutor(max_workers=12) as pool:
            codes = list(
                pool.map(
                    lambda i: client.post("/jobs", json={"kind": "race"}, headers={**USER, "Idempotency-Key": f"race-{i}"}).status_code,
                    range(12),
                )
            )
        assert codes.count(201) == 2
        assert codes.count(429) == 10
        active = db.execute("SELECT count(*) AS n FROM jobs WHERE status IN ('queued', 'running')").fetchone()["n"]
        assert active == 2

    def test_concurrent_retry_double_click_wins_once(self, client, seeded, db):
        db.execute("INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES (1, 1, 'report', 'failed', 1)")
        db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: client.post("/jobs/1/retry", headers=USER).status_code, range(2)))
        assert sorted(codes) == [200, 409]
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "queued"

    def test_concurrent_cancel_wins_once(self, client, seeded, db):
        db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (1, 1, 'report', 'queued')")
        db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: client.post("/jobs/1/cancel", headers=USER).status_code, range(2)))
        assert sorted(codes) == [200, 409]

    def test_idempotency_key_race_creates_single_job(self, client, seeded, db):
        headers = {**USER, "Idempotency-Key": "race-key"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            bodies = list(pool.map(lambda _: client.post("/jobs", json={"kind": "race"}, headers=headers).json(), range(4)))
        ids = {b["id"] for b in bodies}
        assert len(ids) == 1
        total = db.execute("SELECT count(*) AS n FROM jobs WHERE idempotency_key = 'race-key'").fetchone()["n"]
        assert total == 1

    def test_idempotency_replay_wins_when_first_request_fills_last_slot(self, client, seeded, db):
        db.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'already-active', 'running')")
        db.commit()
        headers = {**USER, "Idempotency-Key": "last-slot-key"}

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post("/jobs", json={"kind": "race"}, headers=headers), range(4)))

        assert {response.status_code for response in responses} == {201}
        assert len({response.json()["id"] for response in responses}) == 1
        assert db.execute("SELECT count(*) AS n FROM jobs WHERE idempotency_key = 'last-slot-key'").fetchone()["n"] == 1


class TestDatabaseGuardrails:
    """Quebra deliberada das regras direto no banco: as constraints seguram."""

    def test_invalid_status_rejected_by_check_constraint(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'x', 'exploded')")
        db.rollback()

    def test_duplicate_result_rejected_by_unique(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (1, 1, 'x', 'done')")
        db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'a')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'b')")
        db.rollback()

    def test_duplicate_idempotency_key_rejected_per_company(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        db.execute("INSERT INTO jobs (company_id, kind, status, idempotency_key) VALUES (1, 'x', 'queued', 'k1')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("INSERT INTO jobs (company_id, kind, status, idempotency_key) VALUES (1, 'y', 'queued', 'k1')")
        db.rollback()

    def test_result_requires_existing_job(self, db):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("INSERT INTO job_results (job_id, payload) VALUES (999, 'orfao')")
        db.rollback()

    @pytest.mark.parametrize("attempts", [-1, 4])
    def test_attempts_outside_domain_rejected(self, db, attempts):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO jobs (company_id, kind, attempts) VALUES (1, 'report', %s)", (attempts,))
        db.rollback()

    def test_non_positive_concurrency_limit_rejected(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO companies (name, max_concurrent_jobs) VALUES ('Acme', 0)")
        db.rollback()

    def test_unknown_database_role_rejected(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO users (company_id, email, role) VALUES (1, 'user@acme.test', 'owner')")
        db.rollback()

    def test_blank_company_name_rejected(self, db):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO companies (name) VALUES ('   ')")
        db.rollback()

    def test_control_character_in_kind_rejected(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO jobs (company_id, kind) VALUES (1, E'a\\nb')")
        db.rollback()

    @pytest.mark.parametrize("key", ["   ", "x" * 201])
    def test_invalid_idempotency_key_rejected(self, db, key):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "INSERT INTO jobs (company_id, kind, idempotency_key) VALUES (1, 'report', %s)",
                (key,),
            )
        db.rollback()

    def test_job_creator_must_belong_to_same_company(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme'), (2, 'Globex')")
        db.execute("INSERT INTO users (id, company_id, email) VALUES (1, 2, 'user@globex.test')")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("INSERT INTO jobs (company_id, created_by, kind) VALUES (1, 1, 'report')")
        db.rollback()

    def test_dead_letter_is_unique_per_job(self, db):
        db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        db.execute("INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES (1, 1, 'report', 'failed', 3)")
        db.execute("INSERT INTO dead_letter_jobs (job_id, company_id, kind, attempts) VALUES (1, 1, 'report', 3)")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("INSERT INTO dead_letter_jobs (job_id, company_id, kind, attempts) VALUES (1, 1, 'report', 3)")
        db.rollback()
