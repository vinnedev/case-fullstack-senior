import os
from pathlib import Path
from typing import LiteralString, cast

import psycopg

from shared.config.settings import get_settings


def migrations_dir() -> Path:
    configured = os.environ.get("MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def pending_migrations(conn: psycopg.Connection, directory: Path) -> list[Path]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    return [f for f in sorted(directory.glob("*.sql")) if f.stem not in applied]


def run_migrations(database_url: str | None = None, directory: Path | None = None) -> list[str]:
    directory = directory or migrations_dir()
    applied: list[str] = []
    with psycopg.connect(database_url or get_settings().database_url) as conn:
        for migration in pending_migrations(conn, directory):
            conn.execute(cast(LiteralString, migration.read_text()))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (migration.stem,))
            conn.commit()
            applied.append(migration.stem)
    return applied


if __name__ == "__main__":
    for version in run_migrations():
        print(f"applied {version}")
