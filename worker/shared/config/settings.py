import os
from functools import lru_cache

from pydantic import BaseModel, Field, field_validator

from shared.config.env import load_env


class Settings(BaseModel):
    model_config = {"frozen": True}

    database_url: str
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_s: float = Field(default=30.0, gt=0)
    db_pool_recycle_s: int = Field(default=1800, ge=0)
    log_slow_threshold_ms: float = Field(default=1000.0, ge=0)
    log_success_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("database_url")
    @classmethod
    def database_url_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL não pode ser vazia")
        return value


def _from_environ() -> Settings:
    env = {name: os.environ[name.upper()] for name in Settings.model_fields if name.upper() in os.environ}
    return Settings(**env)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env()
    return _from_environ()
