import argparse
import os
from pathlib import Path
from typing import LiteralString, cast

import psycopg

from shared.config.settings import get_settings

DOWN_SUFFIX = ".down.sql"


def migrations_dir() -> Path:
    configured = os.environ.get("MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def _ensure_ledger(conn: psycopg.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def _applied_versions(conn: psycopg.Connection) -> list[str]:
    _ensure_ledger(conn)
    return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def pending_migrations(conn: psycopg.Connection, directory: Path) -> list[Path]:
    applied = set(_applied_versions(conn))
    return [f for f in sorted(directory.glob("*.sql")) if not f.name.endswith(DOWN_SUFFIX) and f.stem not in applied]


def _execute_sql_file(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text()
    if "-- migration: non-transactional" in sql:
        conn.commit()
        conn.autocommit = True
        try:
            conn.execute(cast(LiteralString, sql))
        finally:
            conn.autocommit = False
    else:
        conn.execute(cast(LiteralString, sql))


def run_migrations(database_url: str | None = None, directory: Path | None = None) -> list[str]:
    directory = directory or migrations_dir()
    applied: list[str] = []
    with psycopg.connect(database_url or get_settings().database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('relay_migrations'))")
        conn.commit()
        try:
            for migration in pending_migrations(conn, directory):
                _execute_sql_file(conn, migration)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (migration.stem,))
                conn.commit()
                applied.append(migration.stem)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('relay_migrations'))")
            conn.commit()
    return applied


def rollback_migrations(database_url: str | None = None, directory: Path | None = None, steps: int = 1) -> list[str]:
    directory = directory or migrations_dir()
    reverted: list[str] = []
    with psycopg.connect(database_url or get_settings().database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('relay_migrations'))")
        conn.commit()
        try:
            for version in reversed(_applied_versions(conn)[-steps:] if steps > 0 else []):
                down_file = directory / f"{version}{DOWN_SUFFIX}"
                if not down_file.is_file():
                    raise FileNotFoundError(f"migration '{version}' não tem arquivo de reversão: {down_file.name}")
                _execute_sql_file(conn, down_file)
                conn.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
                conn.commit()
                reverted.append(version)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('relay_migrations'))")
            conn.commit()
    return reverted


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner de migrations (SQL cru, ledger em schema_migrations)")
    parser.add_argument("command", nargs="?", choices=["up", "down"], default="up")
    parser.add_argument("--steps", type=int, default=1, help="quantas migrations reverter no down (default 1)")
    parser.add_argument("--all", action="store_true", help="reverte todas as migrations aplicadas")
    args = parser.parse_args()

    if args.command == "up":
        for version in run_migrations():
            print(f"applied {version}")
        return
    steps = 10_000 if args.all else args.steps
    for version in rollback_migrations(steps=steps):
        print(f"reverted {version}")


if __name__ == "__main__":
    main()
