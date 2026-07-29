import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer

from shared.db.migrate import run_migrations
from shared.db.pool import connect_dict, get_db
from shared.http.graceful_shutdown import GracefulShutdown

TABLES = ("job_audit_events", "dead_letter_jobs", "job_results", "jobs", "users", "companies")


@pytest.fixture(scope="session")
def test_database():
    container = (
        DockerContainer("postgres:16")
        .with_env("POSTGRES_USER", "relay")
        .with_env("POSTGRES_PASSWORD", "relay")
        .with_env("POSTGRES_DB", "relay_test")
        .with_exposed_ports(5432)
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        url = f"postgresql://relay:relay@{host}:{port}/relay_test"
        # prontidão via tentativa de conexão real (não depende de heurística de log)
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(url, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        run_migrations(database_url=url)
        yield url


@pytest.fixture()
def db(test_database):
    with connect_dict(test_database) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn


@pytest.fixture()
def client(db, test_database):
    import main

    def override_get_db():
        with connect_dict(test_database) as conn:
            yield conn
            conn.commit()

    main.shutdown = GracefulShutdown(main.SERVICE)
    main.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
