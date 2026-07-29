import os
from pathlib import Path


def find_env_file(service_root: Path | None = None) -> Path | None:
    root = (service_root or Path(__file__).resolve().parents[2]).resolve()
    candidate = root / ".env"
    return candidate if candidate.is_file() else None


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
