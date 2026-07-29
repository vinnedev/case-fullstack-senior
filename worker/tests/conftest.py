import os
import subprocess
import time
from pathlib import Path

import psycopg
import pytest
from testcontainers.core.container import DockerContainer

from shared.db.pool import connect_dict

TABLES = ("dead_letter_jobs", "job_results", "jobs", "users", "companies")
API_DIR = Path(__file__).resolve().parents[2] / "api"


def run_api_migrations(database_url: str) -> None:
    env = os.environ | {"DATABASE_URL": database_url}
    subprocess.run(
        ["uv", "run", "python", "-m", "shared.db.migrate"],
        cwd=API_DIR,
        env=env,
        check=True,
    )


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
        run_api_migrations(url)
        yield url


@pytest.fixture()
def db(test_database):
    with connect_dict(test_database) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn
