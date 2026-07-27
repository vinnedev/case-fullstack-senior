import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from features.companies.models import Company
from features.jobs.models import Job, JobResult
from shared.db import engine as engine_module
from shared.db.base import Base


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(engine_module, "_session_factory", factory)
    yield factory
    engine.dispose()


def test_database_url_converts_to_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    assert engine_module.database_url() == "postgresql+psycopg://u:p@h:5432/d"


def test_session_scope_commits_on_success(session_factory):
    with engine_module.session_scope() as session:
        session.add(Company(name="Acme"))
    with session_factory() as session:
        company = session.execute(select(Company)).scalar_one()
        assert company.name == "Acme"
        assert company.max_concurrent_jobs == 2


def test_session_scope_rolls_back_on_error(session_factory):
    with pytest.raises(RuntimeError):
        with engine_module.session_scope() as session:
            session.add(Company(name="Acme"))
            raise RuntimeError("boom")
    with session_factory() as session:
        assert session.execute(select(Company)).first() is None


def test_job_and_result_relationship(session_factory):
    with engine_module.session_scope() as session:
        company = Company(name="Acme")
        session.add(company)
        session.flush()
        job = Job(company_id=company.id, kind="report")
        session.add(job)
        session.flush()
        session.add(JobResult(job_id=job.id, payload="ok"))
    with session_factory() as session:
        job = session.execute(select(Job)).scalar_one()
        assert job.status == "queued"
        assert job.results[0].payload == "ok"


def test_dispose_engine_resets_singletons(monkeypatch):
    monkeypatch.setattr(engine_module, "_engine", None)
    monkeypatch.setattr(engine_module, "_session_factory", None)
    engine_module.dispose_engine()
    assert engine_module._engine is None
    assert engine_module._session_factory is None
