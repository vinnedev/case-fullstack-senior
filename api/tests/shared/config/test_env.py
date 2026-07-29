import os

import pytest

from shared.config.env import find_env_file, load_env, parse_env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY", raising=False)


def test_parse_env_file_reads_key_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_ENV_KEY=abc\nOTHER=123\n")
    assert parse_env_file(env_file) == {"TEST_ENV_KEY": "abc", "OTHER": "123"}


def test_parse_env_file_ignores_comments_blank_and_malformed_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nnoequals\nTEST_ENV_KEY=abc\n=novalue\n")
    assert parse_env_file(env_file) == {"TEST_ENV_KEY": "abc"}


def test_parse_env_file_strips_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('TEST_ENV_KEY="quoted"\n')
    assert parse_env_file(env_file) == {"TEST_ENV_KEY": "quoted"}


def test_load_env_sets_missing_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_ENV_KEY=fromfile\n")
    load_env(env_file)
    assert os.environ["TEST_ENV_KEY"] == "fromfile"


def test_load_env_does_not_override_existing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ENV_KEY", "fromenv")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_ENV_KEY=fromfile\n")
    load_env(env_file)
    assert os.environ["TEST_ENV_KEY"] == "fromenv"


def test_load_env_missing_file_is_noop(tmp_path):
    assert load_env(tmp_path / ".env") == {}


def test_find_env_file_reads_service_root(tmp_path):
    (tmp_path / ".env").write_text("TEST_ENV_KEY=abc\n")
    assert find_env_file(tmp_path) == tmp_path / ".env"


def test_find_env_file_does_not_search_parent_directories(tmp_path):
    (tmp_path / ".env").write_text("TEST_ENV_KEY=parent\n")
    service_root = tmp_path / "api"
    service_root.mkdir()
    assert find_env_file(service_root) is None


def test_find_env_file_returns_none_when_absent(tmp_path):
    assert find_env_file(tmp_path) is None
