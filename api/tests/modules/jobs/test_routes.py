from pathlib import Path

import pytest

HEADERS = {"X-Auth": "1:user"}


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20), (2, 'Globex', 2, 100)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES
          (1, 1, 'report', 'done', 1),
          (2, 1, 'import', 'queued', 0),
          (3, 2, 'report', 'done', 1),
          (4, 1, 'report', 'failed', 1),
          (5, 1, 'report', 'failed', 3)
        """
    )
    db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'resultado 1')")
    db.execute("SELECT setval('jobs_id_seq', 100)")
    db.commit()


def test_list_jobs_returns_only_company_jobs_with_result_count(client, seeded):
    resp = client.get("/jobs", headers=HEADERS)
    assert resp.status_code == 200
    by_id = {j["id"]: j for j in resp.json()}
    assert set(by_id) == {1, 2, 4, 5}
    assert by_id[1]["result_count"] == 1
    assert by_id[2]["result_count"] == 0


def test_list_jobs_requires_auth(client):
    assert client.get("/jobs").status_code == 401


def test_list_jobs_paginates(client, seeded):
    assert len(client.get("/jobs?limit=2", headers=HEADERS).json()) == 2
    assert len(client.get("/jobs?limit=200&offset=2", headers=HEADERS).json()) == 2
    assert client.get("/jobs?limit=0", headers=HEADERS).status_code == 422
    assert client.get("/jobs?limit=201", headers=HEADERS).status_code == 422


def test_get_job_found(client, seeded):
    resp = client.get("/jobs/1", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1 and body["status"] == "done" and body["attempts"] == 1


def test_get_job_not_found(client, seeded):
    assert client.get("/jobs/999", headers=HEADERS).status_code == 404


def test_get_job_isolated_by_tenant(client, seeded):
    assert client.get("/jobs/3", headers=HEADERS).status_code == 404


def test_get_result_found(client, seeded):
    assert client.get("/jobs/1/result", headers=HEADERS).json() == {"payload": "resultado 1"}


def test_get_result_missing(client, seeded):
    assert client.get("/jobs/2/result", headers=HEADERS).status_code == 404


def test_get_result_isolated_by_tenant(client, seeded):
    assert client.get("/jobs/3/result", headers={"X-Auth": "2:user"}).status_code == 404
    assert client.get("/jobs/3/result", headers=HEADERS).status_code == 404


def test_create_job_queued(client, seeded):
    resp = client.post("/jobs", json={"kind": "report"}, headers={"X-Auth": "2:user", "Idempotency-Key": "t-test_routes-1"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


def test_create_job_records_submission_audit(client, seeded):
    created = client.post(
        "/jobs",
        json={"kind": "report"},
        headers={"X-Auth": "2:user", "Idempotency-Key": "t-test_routes-audit"},
    ).json()
    detail = client.get(f"/jobs/{created['id']}", headers={"X-Auth": "2:user"}).json()
    assert detail["audit_events"][0]["event_type"] == "submitted"
    assert detail["audit_events"][0]["actor"] == "2:user"
    assert detail["audit_events"][0]["trace_id"]


def test_create_job_respects_concurrency_limit(client, seeded, db):
    db.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'x', 'running')")
    db.commit()
    assert client.post("/jobs", json={"kind": "report"}, headers={**HEADERS, "Idempotency-Key": "t-test_routes-2"}).status_code == 429


def test_create_job_rejects_empty_kind(client, seeded):
    assert client.post("/jobs", json={"kind": ""}, headers={**HEADERS, "Idempotency-Key": "t-test_routes-3"}).status_code == 422


def test_create_job_unknown_company(client, seeded):
    headers = {"X-Auth": "99:user", "Idempotency-Key": "k-unknown"}
    assert client.post("/jobs", json={"kind": "report"}, headers=headers).status_code == 401


def test_cancel_queued_job(client, seeded):
    resp = client.post("/jobs/2/cancel", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"id": 2, "status": "cancelled"}
    assert client.get("/jobs/2", headers=HEADERS).json()["status"] == "cancelled"


def test_cancel_records_audit_in_job_detail(client, seeded):
    client.post("/jobs/2/cancel", headers=HEADERS)
    detail = client.get("/jobs/2", headers=HEADERS).json()
    assert detail["cancellation"]["cancelled_by"] == "1:user"
    assert detail["cancellation"]["cancelled_at"]
    assert detail["audit_events"][0]["event_type"] == "cancelled"
    assert detail["audit_events"][0]["trace_id"]


def test_cancel_running_job(client, seeded, db):
    db.execute("UPDATE jobs SET status = 'running' WHERE id = 2")
    db.commit()
    assert client.post("/jobs/2/cancel", headers=HEADERS).status_code == 200


def test_cancel_done_job_conflicts(client, seeded):
    assert client.post("/jobs/1/cancel", headers=HEADERS).status_code == 409


def test_cancel_cancelled_job_conflicts(client, seeded):
    client.post("/jobs/2/cancel", headers=HEADERS)
    assert client.post("/jobs/2/cancel", headers=HEADERS).status_code == 409


def test_cancel_isolated_by_tenant(client, seeded):
    assert client.post("/jobs/3/cancel", headers=HEADERS).status_code == 404


def test_retry_failed_job(client, seeded, db):
    resp = client.post("/jobs/4/retry", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued" and body["attempts"] == 1
    delay = db.execute("SELECT extract(epoch FROM next_attempt_at - now()) AS seconds FROM jobs WHERE id = 4").fetchone()["seconds"]
    assert 4.0 <= float(delay) <= 6.0


def test_retry_records_audit_in_job_detail(client, seeded):
    client.post("/jobs/4/retry", headers=HEADERS)
    detail = client.get("/jobs/4", headers=HEADERS).json()
    assert detail["audit_events"][0]["event_type"] == "retry_requested"
    assert detail["audit_events"][0]["actor"] == "1:user"
    assert detail["audit_events"][0]["trace_id"]


def test_retry_twice_conflicts(client, seeded):
    assert client.post("/jobs/4/retry", headers=HEADERS).status_code == 200
    assert client.post("/jobs/4/retry", headers=HEADERS).status_code == 409


def test_retry_respects_max_attempts(client, seeded):
    assert client.post("/jobs/5/retry", headers=HEADERS).status_code == 409


def test_retry_non_failed_job_conflicts(client, seeded):
    assert client.post("/jobs/1/retry", headers=HEADERS).status_code == 409


def test_retry_isolated_by_tenant(client, seeded):
    assert client.post("/jobs/3/retry", headers=HEADERS).status_code == 404


def test_list_jobs_exposes_total_count_header(client, seeded):
    resp = client.get("/jobs?limit=2", headers=HEADERS)
    assert resp.headers["X-Total-Count"] == "4"


def test_list_jobs_filters_by_status(client, seeded):
    resp = client.get("/jobs?status=failed", headers=HEADERS)
    assert {j["status"] for j in resp.json()} == {"failed"}
    assert resp.headers["X-Total-Count"] == "2"
    assert client.get("/jobs?status=invalido", headers=HEADERS).status_code == 422


def test_retry_backfills_missing_trace_id(client, seeded, db):
    # jobs legados (seed) nascem sem trace; o retry os torna rastreáveis
    resp = client.post("/jobs/4/retry", headers=HEADERS)
    assert resp.status_code == 200
    stored = db.execute("SELECT trace_id FROM jobs WHERE id = 4").fetchone()["trace_id"]
    assert stored == resp.headers["X-Request-ID"]


def test_retry_preserves_existing_trace_id(client, seeded, db):
    db.execute("UPDATE jobs SET trace_id = 'trace-original' WHERE id = 4")
    db.commit()
    assert client.post("/jobs/4/retry", headers=HEADERS).status_code == 200
    assert db.execute("SELECT trace_id FROM jobs WHERE id = 4").fetchone()["trace_id"] == "trace-original"


def test_create_job_without_idempotency_key_generates_one(client, seeded, db):
    resp = client.post("/jobs", json={"kind": "report"}, headers={"X-Auth": "2:user"})
    assert resp.status_code == 201
    stored = db.execute("SELECT idempotency_key FROM jobs WHERE id = %s", (resp.json()["id"],)).fetchone()
    assert stored["idempotency_key"].startswith("srv-")


def test_create_job_replay_with_different_kind_is_conflict(client, seeded):
    headers = {"X-Auth": "2:user", "Idempotency-Key": "t-test_routes-mismatch"}
    assert client.post("/jobs", json={"kind": "report"}, headers=headers).status_code == 201
    resp = client.post("/jobs", json={"kind": "outro"}, headers=headers)
    assert resp.status_code == 409
    assert "payload diferente" in resp.json()["detail"]


def test_create_job_requires_auth_header(client, seeded):
    resp = client.post("/jobs", json={"kind": "report"}, headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 401


FRONTEND_ICON = Path(__file__).resolve().parents[4] / "web" / "public" / "galaxies-icon.png"


def test_docs_favicon_is_served_as_png(client):
    resp = client.get("/favicon.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 0


@pytest.mark.skipif(not FRONTEND_ICON.is_file(), reason="repositório completo indisponível (build isolado da api)")
def test_docs_favicon_matches_frontend_icon(client):
    # mesma identidade visual em /docs e na web: bytes idênticos, não "parecido"
    assert client.get("/favicon.png").content == FRONTEND_ICON.read_bytes()


def test_docs_page_references_the_shared_favicon(client):
    html = client.get("/docs").text
    assert '<link rel="icon" type="image/png" href="/favicon.png">' in html
