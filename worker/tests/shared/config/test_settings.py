import pytest
from pydantic import ValidationError

from shared.config.settings import Settings, _from_environ, get_settings


def test_defaults_applied():
    s = Settings(database_url="postgresql://x")
    assert s.db_pool_size == 5
    assert s.db_max_overflow == 10
    assert s.log_success_sample_rate == 1.0


def test_reads_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env")
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("LOG_SUCCESS_SAMPLE_RATE", "0.25")
    s = _from_environ()
    assert s.database_url == "postgresql://env"
    assert s.db_pool_size == 7
    assert s.log_success_sample_rate == 0.25


def test_rejects_invalid_values(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x", db_pool_size=0)
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x", db_pool_size=2, db_max_overflow=0)
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x", log_success_sample_rate=1.5)
    with pytest.raises(ValidationError):
        Settings(database_url="   ")


def test_settings_are_frozen():
    s = Settings(database_url="postgresql://x")
    with pytest.raises(ValidationError):
        s.db_pool_size = 99


def test_get_settings_is_cached(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql://cached")
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
