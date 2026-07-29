import pytest


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme'), (2, 'Globex')")
    db.execute("INSERT INTO jobs (id, company_id, kind, status) VALUES (1, 1, 'report', 'done'), (2, 2, 'report', 'queued')")
    db.commit()


def test_admin_jobs_returns_all_companies(client, seeded):
    resp = client.get("/admin/jobs", headers={"X-Auth": "1:admin"})
    assert resp.status_code == 200
    assert {j["company_id"] for j in resp.json()} == {1, 2}


def test_admin_jobs_paginates(client, seeded):
    resp = client.get("/admin/jobs?limit=1&offset=1", headers={"X-Auth": "1:admin"})
    assert resp.status_code == 200
    assert [j["id"] for j in resp.json()] == [2]


def test_admin_jobs_rejects_invalid_pagination(client, seeded):
    assert client.get("/admin/jobs?offset=-1", headers={"X-Auth": "1:admin"}).status_code == 422
    assert client.get("/admin/dlq?limit=0", headers={"X-Auth": "1:admin"}).status_code == 422


def test_admin_jobs_requires_admin_role(client, seeded):
    assert client.get("/admin/jobs", headers={"X-Auth": "1:user"}).status_code == 403


def test_admin_jobs_requires_auth(client, seeded):
    assert client.get("/admin/jobs").status_code == 401


def test_dlq_requires_admin_and_lists_details(client, seeded, db):
    db.execute(
        """
        INSERT INTO dead_letter_jobs (job_id, company_id, kind, attempts, last_error, trace_id, job_created_at)
        VALUES (1, 1, 'report', 3, 'RuntimeError: boom', 'trace-x', now())
        """
    )
    db.commit()
    assert client.get("/admin/dlq", headers={"X-Auth": "1:user"}).status_code == 403
    rows = client.get("/admin/dlq", headers={"X-Auth": "1:admin"}).json()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["attempts"] == 3
    assert entry["last_error"] == "RuntimeError: boom"
    assert entry["trace_id"] == "trace-x"
    assert entry["failed_at"]
