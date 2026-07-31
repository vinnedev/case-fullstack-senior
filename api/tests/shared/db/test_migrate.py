"""Ciclo de vida das migrations: up, down parcial, down total e reidempotência.

Usa um banco dedicado no mesmo container para não desmontar o schema
compartilhado pelos demais testes da sessão.
"""

import psycopg
import pytest

from shared.db.migrate import migrations_dir, rollback_migrations, run_migrations


@pytest.fixture()
def migrate_db(test_database):
    admin_url = test_database
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS relay_migrate_test")
        conn.execute("CREATE DATABASE relay_migrate_test")
    yield test_database.rsplit("/", 1)[0] + "/relay_migrate_test"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS relay_migrate_test")


def applied_versions(url):
    with psycopg.connect(url) as conn:
        return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def table_names(url):
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
        return {row[0] for row in rows}


def test_every_migration_has_a_down_file():
    ups = {f.stem for f in migrations_dir().glob("*.sql") if not f.name.endswith(".down.sql")}
    downs = {f.name.removesuffix(".down.sql") for f in migrations_dir().glob("*.down.sql")}
    assert ups == downs, f"sem down: {sorted(ups - downs)}; down órfão: {sorted(downs - ups)}"


def test_up_down_full_cycle(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    assert len(applied) >= 11
    assert "jobs" in table_names(migrate_db)

    reverted = rollback_migrations(database_url=migrate_db, steps=len(applied))
    assert reverted == list(reversed(applied))
    assert applied_versions(migrate_db) == []
    assert table_names(migrate_db) == {"schema_migrations"}

    assert run_migrations(database_url=migrate_db) == applied


def test_down_single_step_reverts_only_the_last(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    assert rollback_migrations(database_url=migrate_db, steps=1) == [applied[-1]]
    assert applied_versions(migrate_db) == applied[:-1]
    assert run_migrations(database_url=migrate_db) == [applied[-1]]


def test_down_preserves_data_of_untouched_migrations(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    with psycopg.connect(migrate_db) as conn:
        conn.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        conn.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'report', 'cancelled')")
        conn.commit()
    rollback_migrations(database_url=migrate_db, steps=len(applied) - 3)
    with psycopg.connect(migrate_db) as conn:
        row = conn.execute("SELECT status, last_error FROM jobs").fetchone()
        assert row is not None and row[0] == "failed"
        assert row[1] is not None and "0004" in row[1]
        companies = conn.execute("SELECT count(*) FROM companies").fetchone()
        assert companies is not None and companies[0] == 1


def test_down_without_file_fails_loudly(migrate_db, tmp_path):
    run_migrations(database_url=migrate_db)
    with psycopg.connect(migrate_db) as conn:
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('9999_sem_down')")
        conn.commit()
    with pytest.raises(FileNotFoundError, match="9999_sem_down"):
        rollback_migrations(database_url=migrate_db, steps=1)
    assert "9999_sem_down" in applied_versions(migrate_db)


def test_down_files_are_never_applied_as_up(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    assert all(not version.endswith(".down") for version in applied)


def test_nontransactional_delimiter_preserves_semicolons(migrate_db, tmp_path):
    migration = tmp_path / "0001_delimiter.sql"
    migration.write_text(
        """
        -- migration: non-transactional
        CREATE TABLE delimiter_values (value TEXT NOT NULL DEFAULT 'a;b');
        -- migration: next-statement
        INSERT INTO delimiter_values (value) VALUES ('c;d');
        """
    )

    assert run_migrations(database_url=migrate_db, directory=tmp_path) == ["0001_delimiter"]
    with psycopg.connect(migrate_db) as conn:
        assert conn.execute("SELECT value FROM delimiter_values").fetchone() == ("c;d",)


def test_nontransactional_index_migration_recovers_invalid_index_without_ledger(migrate_db):
    run_migrations(database_url=migrate_db)
    with psycopg.connect(migrate_db) as conn:
        conn.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        conn.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'report', 'queued'), (1, 'report', 'queued')")
        conn.commit()
        conn.autocommit = True
        conn.execute("DROP INDEX CONCURRENTLY ix_jobs_queued_ready")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                """
                CREATE UNIQUE INDEX CONCURRENTLY ix_jobs_queued_ready
                ON jobs (company_id)
                WHERE status = 'queued' AND next_attempt_at IS NULL
                """
            )
        conn.autocommit = False
        conn.execute("DELETE FROM schema_migrations WHERE version = '0014_job_scheduling_indexes'")
        conn.commit()

    assert run_migrations(database_url=migrate_db) == ["0014_job_scheduling_indexes"]
    with psycopg.connect(migrate_db) as conn:
        index = conn.execute(
            """
            SELECT i.indisvalid, pg_get_indexdef(i.indexrelid)
            FROM pg_index AS i
            JOIN pg_class AS c ON c.oid = i.indexrelid
            WHERE c.relname = 'ix_jobs_queued_ready'
            """
        ).fetchone()
        assert index is not None and index[0] is True
        assert "ON public.jobs USING btree (id)" in index[1]
        assert "0014_job_scheduling_indexes" in applied_versions(migrate_db)
        conn.execute("SET enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                """
                EXPLAIN (COSTS OFF)
                SELECT id FROM jobs
                WHERE status = 'queued' AND next_attempt_at IS NULL
                ORDER BY id
                LIMIT 1
                """
            )
        )
        assert "ix_jobs_queued_ready" in plan


def test_second_run_is_a_noop(migrate_db):
    first = run_migrations(database_url=migrate_db)
    assert len(first) >= 11
    tables_after_first = table_names(migrate_db)
    assert run_migrations(database_url=migrate_db) == []
    assert applied_versions(migrate_db) == first
    assert table_names(migrate_db) == tables_after_first


def test_concurrent_runners_apply_each_migration_exactly_once(migrate_db):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_migrations(database_url=migrate_db), range(2)))
    applied = [version for result in results for version in result]
    assert sorted(applied) == applied_versions(migrate_db)  # sem duplicata entre os dois runners
    assert "0014_job_scheduling_indexes" in applied
    with psycopg.connect(migrate_db) as conn:
        row = conn.execute("SELECT count(*), count(DISTINCT version) FROM schema_migrations").fetchone()
        assert row is not None and row[0] == row[1]
