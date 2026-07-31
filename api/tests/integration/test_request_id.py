"""X-Request-ID: correlação resposta ↔ log ↔ trace_id do job."""

import json
import uuid

import pytest

import main

HEADERS = {"X-Auth": "1:user"}


@pytest.fixture()
def seeded(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 2, 20)")
    db.commit()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def test_success_response_carries_request_id(client, seeded):
    resp = client.get("/jobs", headers=HEADERS)
    assert resp.status_code == 200
    assert _is_uuid(resp.headers["X-Request-ID"])


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        (None, 401),
        (HEADERS, 404),
    ],
)
def test_error_responses_carry_request_id(client, seeded, headers, expected_status):
    resp = client.get("/jobs/999999", headers=headers)
    assert resp.status_code == expected_status
    assert _is_uuid(resp.headers["X-Request-ID"])


def test_request_id_of_create_becomes_job_trace_id(client, seeded, db):
    created = client.post("/jobs", json={"kind": "report"}, headers={**HEADERS, "Idempotency-Key": "req-id-1"})
    assert created.status_code == 201
    request_id = created.headers["X-Request-ID"]
    job_id = created.json()["id"]
    assert db.execute("SELECT trace_id FROM jobs WHERE id = %s", (job_id,)).fetchone()["trace_id"] == request_id
    detail = client.get(f"/jobs/{job_id}", headers=HEADERS).json()
    assert detail["audit_events"][0]["trace_id"] == request_id


def test_each_request_gets_a_fresh_request_id(client, seeded):
    first = client.get("/jobs", headers=HEADERS).headers["X-Request-ID"]
    second = client.get("/jobs", headers=HEADERS).headers["X-Request-ID"]
    assert first != second


def test_shutdown_503_carries_and_logs_request_id(client, capsys):
    main.shutdown.shutting_down = True

    origin = "http://localhost:5173"
    response = client.get("/jobs", headers={**HEADERS, "Origin": origin})

    assert response.status_code == 503
    assert response.headers["Access-Control-Allow-Origin"] == origin
    exposed_headers = {header.strip().lower() for header in response.headers["Access-Control-Expose-Headers"].split(",")}
    assert "x-request-id" in exposed_headers
    request_id = response.headers["X-Request-ID"]
    assert _is_uuid(request_id)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    event = next(item for item in events if item.get("event") == "http_request")
    assert event["request_id"] == request_id
    assert event["http_status"] == 503
    assert event["shutdown_rejected"] is True
