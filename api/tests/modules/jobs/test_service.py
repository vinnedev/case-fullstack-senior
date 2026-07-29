import pytest

from modules.jobs.errors import (
    CompanyUnknownError,
    ConcurrencyLimitError,
    InvalidJobStateError,
    JobNotFoundError,
    ResultNotFoundError,
    RetryLimitError,
)
from modules.jobs.service import JobsService


@pytest.fixture()
def service(db):
    db.execute("INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES (1, 'Acme', 1, 20)")
    db.execute(
        """
        INSERT INTO jobs (id, company_id, kind, status, attempts) VALUES
          (1, 1, 'report', 'done', 1),
          (2, 1, 'import', 'queued', 0),
          (3, 1, 'report', 'failed', 3)
        """
    )
    db.execute("INSERT INTO job_results (job_id, payload) VALUES (1, 'resultado 1')")
    db.execute("SELECT setval('jobs_id_seq', 100)")
    db.commit()
    return JobsService(db)


def test_list_jobs_returns_jobs_with_result_count(service):
    rows = {r["id"]: r["result_count"] for r in service.list_jobs(company_id=1)}
    assert rows == {1: 1, 2: 0, 3: 0}


def test_list_jobs_isolates_companies(service):
    assert service.list_jobs(company_id=99) == []


def test_get_job_raises_when_missing(service):
    with pytest.raises(JobNotFoundError):
        service.get_job(1, 999)


def test_get_result_raises_when_missing(service):
    with pytest.raises(ResultNotFoundError):
        service.get_result(1, 2)


def test_create_job_rejects_unknown_company(service):
    with pytest.raises(CompanyUnknownError):
        service.create_job(company_id=99, kind="report", trace_id=None, idempotency_key="svc-99")


def test_create_job_enforces_concurrency_limit(service):
    with pytest.raises(ConcurrencyLimitError):
        service.create_job(company_id=1, kind="report", trace_id=None, idempotency_key="svc-1")


def test_cancel_job_only_in_cancellable_states(service):
    assert service.cancel_job(1, 2, "1:user")["status"] == "cancelled"
    with pytest.raises(InvalidJobStateError):
        service.cancel_job(1, 1, "1:user")


def test_retry_job_respects_state_and_limit(service):
    with pytest.raises(RetryLimitError):
        service.retry_job(1, 3)
    with pytest.raises(InvalidJobStateError):
        service.retry_job(1, 1)
