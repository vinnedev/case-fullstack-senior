import os
from pathlib import Path


def find_env_file(start: Path | None = None) -> Path | None:
    current = (start or Path(__file__).resolve().parent).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip("'\"")
    return values


def load_env(env_file: Path | None = None) -> dict[str, str]:
    env_file = env_file or find_env_file()
    if env_file is None or not env_file.is_file():
        return {}
    values = parse_env_file(env_file)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values
