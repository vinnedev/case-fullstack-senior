import pytest

from main import should_emit_request_log

USER = {"X-Auth": "1:user"}
OTHER = {"X-Auth": "2:user"}


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20), (2, 'Globex', 2, 100)")
    db.commit()


class TestExpectedFlows:
    def test_full_job_lifecycle_via_api(self, client, seeded, db):
        created = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-1"}).json()
        job_id = created["id"]
        assert created["status"] == "queued"

        # worker conclui (simulado via SQL — o processamento real é coberto nos testes do worker)
        db.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (job_id,))
        db.execute("INSERT INTO job_results (job_id, payload) VALUES (%s, 'ok')", (job_id,))
        db.commit()

        assert client.get(f"/jobs/{job_id}", headers=USER).json()["status"] == "done"
        assert client.get(f"/jobs/{job_id}/result", headers=USER).json() == {"payload": "ok"}

    def test_cancel_then_retry_conflict(self, client, seeded):
        job_id = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-2"}).json()["id"]
        assert client.post(f"/jobs/{job_id}/cancel", headers=USER).status_code == 200
        assert client.post(f"/jobs/{job_id}/retry", headers=USER).status_code == 409

    def test_failed_job_retry_then_double_click(self, client, seeded, db):
        db.execute("INSERT INTO jobs (id, company_id, kind, status, attempts, last_error) VALUES (10, 1, 'report', 'failed', 1, 'boom')")
        db.execute("SELECT setval('jobs_id_seq', 100)")
        db.commit()
        first = client.post("/jobs/10/retry", headers=USER)
        second = client.post("/jobs/10/retry", headers=USER)
        assert first.status_code == 200
        assert second.status_code == 409
        assert client.get("/jobs/10", headers=USER).json()["last_error"] is None

    def test_trace_id_persisted_on_creation(self, client, seeded, db):
        job_id = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-3"}).json()["id"]
        trace = db.execute("SELECT trace_id FROM jobs WHERE id = %s", (job_id,)).fetchone()["trace_id"]
        assert trace


class TestUnexpectedInputs:
    @pytest.mark.parametrize(
        "path",
        ["/metrics", "/metrics/", "/health", "/healthz", "/ready", "/readyz", "/live", "/livez"],
    )
    def test_probe_log_suppression_matches_operational_routes(self, path):
        assert should_emit_request_log(path, suppress_probe_routes=True) is False
        assert should_emit_request_log(path, suppress_probe_routes=False) is True

    def test_regular_route_is_never_suppressed(self):
        assert should_emit_request_log("/jobs", suppress_probe_routes=True) is True

    def test_health_endpoint(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_probe_flag_suppresses_only_probe_wide_events(self, client, capsys, monkeypatch):
        from types import SimpleNamespace

        import main

        monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(log_suppress_probe_routes=True))
        capsys.readouterr()

        client.get("/health")
        assert '"http_path": "/health"' not in capsys.readouterr().out

        client.get("/jobs", headers=USER)
        assert '"http_path": "/jobs"' in capsys.readouterr().out

    @pytest.mark.parametrize("header", ["", "semformato", ":", "abc:user", "-1:user", "0:user", "1:"])
    def test_malformed_auth_rejected(self, client, seeded, header):
        assert client.get("/jobs", headers={"X-Auth": header}).status_code == 401

    def test_non_numeric_job_id_rejected(self, client, seeded):
        assert client.get("/jobs/abc", headers=USER).status_code == 422

    def test_non_positive_job_id_rejected(self, client, seeded):
        assert client.get("/jobs/-1", headers=USER).status_code == 422
        assert client.get("/jobs/0", headers=USER).status_code == 422

    @pytest.mark.parametrize("kind", [123, None, ["report"], {"a": 1}, True, 1.5])
    def test_kind_wrong_type_rejected_with_field_detail(self, client, seeded, kind):
        resp = client.post("/jobs", json={"kind": kind}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-4"})
        assert resp.status_code == 422
        detail = resp.json()["detail"][0]
        assert detail["type"] == "string_type"
        assert detail["loc"] == ["body", "kind"]

    def test_kind_boundaries(self, client, seeded):
        def create(body: dict) -> int:
            headers = {**USER, "Idempotency-Key": f"kb-{len(str(body))}-{sorted(body.items())!r}"}
            return client.post("/jobs", json=body, headers=headers).status_code

        assert create({"kind": ""}) == 422
        assert create({"kind": "x" * 101}) == 422
        assert create({}) == 422

    def test_sql_injection_in_kind_is_stored_literally(self, client, seeded, db):
        payload = "report'; DROP TABLE jobs; --"
        job_id = client.post("/jobs", json={"kind": payload}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-8"}).json()["id"]
        stored = db.execute("SELECT kind FROM jobs WHERE id = %s", (job_id,)).fetchone()["kind"]
        assert stored == payload
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] >= 1

    def test_tenant_cannot_touch_other_tenant_jobs(self, client, seeded):
        job_id = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-9"}).json()["id"]
        assert client.get(f"/jobs/{job_id}", headers=OTHER).status_code == 404
        assert client.post(f"/jobs/{job_id}/cancel", headers=OTHER).status_code == 404
        assert client.post(f"/jobs/{job_id}/retry", headers=OTHER).status_code == 404

    def test_admin_route_denied_for_non_admin(self, client, seeded):
        assert client.get("/admin/jobs", headers=USER).status_code == 403

    def test_pagination_garbage_rejected(self, client, seeded):
        assert client.get("/jobs?limit=abc", headers=USER).status_code == 422
        assert client.get("/jobs?offset=-1", headers=USER).status_code == 422

    def test_unknown_route_is_404(self, client, seeded):
        assert client.get("/nope", headers=USER).status_code == 404


class TestIdempotencyKey:
    def test_same_key_returns_same_job(self, client, seeded, db):
        from shared.observability.api_metrics import jobs_created_total

        headers = {**USER, "Idempotency-Key": "abc-123"}
        metric_before = jobs_created_total._value.get()
        first = client.post("/jobs", json={"kind": "report"}, headers=headers)
        second = client.post("/jobs", json={"kind": "report"}, headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
        assert jobs_created_total._value.get() - metric_before == 1

    def test_replay_bypasses_concurrency_limit_check(self, client, seeded, db):
        headers = {**USER, "Idempotency-Key": "replay-1"}
        job_id = client.post("/jobs", json={"kind": "report"}, headers=headers).json()["id"]
        db.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'x', 'running'), (1, 'y', 'running')")
        db.commit()
        replay = client.post("/jobs", json={"kind": "report"}, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["id"] == job_id

    def test_different_companies_can_share_key(self, client, seeded):
        a = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "k1"})
        b = client.post("/jobs", json={"kind": "report"}, headers={**OTHER, "Idempotency-Key": "k1"})
        assert a.json()["id"] != b.json()["id"]

    def test_without_key_creates_distinct_jobs(self, client, seeded, db):
        db.execute("UPDATE companies SET max_concurrent_jobs = 10 WHERE id = 1")
        db.commit()
        a = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-10"})
        b = client.post("/jobs", json={"kind": "report"}, headers={**USER, "Idempotency-Key": "t-test_api_behaviors-11"})
        assert a.json()["id"] != b.json()["id"]

    def test_key_replay_does_not_notify_again(self, client, seeded, db, test_database):
        import psycopg

        headers = {**USER, "Idempotency-Key": "notify-once"}
        with psycopg.connect(test_database, autocommit=True) as listen_conn:
            listen_conn.execute("LISTEN jobs_queued")
            client.post("/jobs", json={"kind": "report"}, headers=headers)
            client.post("/jobs", json={"kind": "report"}, headers=headers)
            notifications = list(listen_conn.notifies(timeout=1.0))
        assert len(notifications) == 1
