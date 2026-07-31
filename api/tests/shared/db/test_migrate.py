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

    # up de novo após o down total: ciclo completo reprodutível
    assert run_migrations(database_url=migrate_db) == applied


def test_down_single_step_reverts_only_the_last(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    assert rollback_migrations(database_url=migrate_db, steps=1) == [applied[-1]]
    assert applied_versions(migrate_db) == applied[:-1]
    # reaplicar só a última volta ao estado completo
    assert run_migrations(database_url=migrate_db) == [applied[-1]]


def test_down_preserves_data_of_untouched_migrations(migrate_db):
    run_migrations(database_url=migrate_db)
    with psycopg.connect(migrate_db) as conn:
        conn.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        conn.execute("INSERT INTO jobs (company_id, kind, status) VALUES (1, 'report', 'cancelled')")
        conn.commit()
    # reverte até antes da 0004 (status 'cancelled' deixa de existir no domínio)
    rollback_migrations(database_url=migrate_db, steps=8)
    with psycopg.connect(migrate_db) as conn:
        row = conn.execute("SELECT status, last_error FROM jobs").fetchone()
        assert row is not None and row[0] == "failed"  # mapeado pelo down da 0004
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
    # nada foi revertido: o ledger continua íntegro
    assert "9999_sem_down" in applied_versions(migrate_db)


def test_down_files_are_never_applied_as_up(migrate_db):
    applied = run_migrations(database_url=migrate_db)
    assert all(not version.endswith(".down") for version in applied)
