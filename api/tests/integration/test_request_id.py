"""X-Request-ID: correlação resposta ↔ log ↔ trace_id do job."""

import uuid

import pytest

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
        (None, 401),  # sem X-Auth: erro que não toca job — o header é o único elo com o log
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
