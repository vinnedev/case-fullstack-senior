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


def test_admin_jobs_keeps_original_case_contract(client, seeded):
    rows = client.get("/admin/jobs", headers={"X-Auth": "1:admin"}).json()
    assert [set(row) for row in rows] == [{"id", "company_id", "status"}] * len(rows)
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)


def test_admin_jobs_filters_by_company_and_status(client, seeded):
    admin = {"X-Auth": "1:admin"}
    by_company = client.get("/admin/jobs?company_id=2", headers=admin)
    assert [j["id"] for j in by_company.json()] == [2]
    assert by_company.headers["X-Total-Count"] == "1"
    assert client.get("/admin/jobs?company_id=2&status=done", headers=admin).json() == []
    assert client.get("/admin/jobs?company_id=0", headers=admin).status_code == 422
    assert client.get("/admin/jobs?status=invalido", headers=admin).status_code == 422


def test_admin_jobs_rejects_invalid_pagination(client, seeded):
    assert client.get("/admin/jobs?offset=-1", headers={"X-Auth": "1:admin"}).status_code == 422
    assert client.get("/admin/dlq?limit=0", headers={"X-Auth": "1:admin"}).status_code == 422


def test_admin_companies_lists_processing_summary(client, seeded):
    resp = client.get("/admin/companies", headers={"X-Auth": "1:admin"})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "2"
    acme, globex = resp.json()
    assert acme["name"] == "Acme" and acme["total_jobs"] == 1 and acme["done"] == 1
    assert globex["name"] == "Globex" and globex["queued"] == 1 and globex["done"] == 0


def test_admin_companies_paginates(client, seeded):
    resp = client.get("/admin/companies?limit=1&offset=1", headers={"X-Auth": "1:admin"})
    assert [c["id"] for c in resp.json()] == [2]
    assert resp.headers["X-Total-Count"] == "2"
    assert client.get("/admin/companies?limit=0", headers={"X-Auth": "1:admin"}).status_code == 422


def test_admin_companies_requires_admin_role(client, seeded):
    assert client.get("/admin/companies", headers={"X-Auth": "1:user"}).status_code == 403
    assert client.get("/admin/companies").status_code == 401


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
