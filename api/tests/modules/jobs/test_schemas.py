import pytest
from pydantic import ValidationError

from modules.jobs.schemas import JobCreated, JobDetail, NewJob


@pytest.mark.parametrize("kind", ["a\u0000b", "a\u001fb", "a\u007fb", "a\u0085b"])
def test_new_job_rejects_control_characters(kind):
    with pytest.raises(ValidationError):
        NewJob.model_validate({"kind": kind})


def test_new_job_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        NewJob.model_validate({"kind": "report", "status": "done"})


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 0, "company_id": 1, "kind": "report", "status": "done", "attempts": 1},
        {"id": 1, "company_id": 0, "kind": "report", "status": "done", "attempts": 1},
        {"id": 1, "company_id": 1, "kind": "report", "status": "exploded", "attempts": 1},
        {"id": 1, "company_id": 1, "kind": "report", "status": "done", "attempts": -1},
        {"id": 1, "company_id": 1, "kind": "report", "status": "done", "attempts": 4},
    ],
)
def test_job_detail_rejects_broken_invariants(payload):
    with pytest.raises(ValidationError):
        JobDetail.model_validate(payload)


@pytest.mark.parametrize("payload", [{"id": 0, "status": "queued"}, {"id": 1, "status": "done"}])
def test_job_created_rejects_broken_invariants(payload):
    with pytest.raises(ValidationError):
        JobCreated.model_validate(payload)
