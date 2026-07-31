import os
from functools import lru_cache

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.config.env import load_env


class Settings(BaseModel):
    model_config = {"frozen": True}

    database_url: str
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_s: float = Field(default=30.0, gt=0)
    db_pool_recycle_s: int = Field(default=1800, ge=0)
    cors_origins: str = "http://localhost:5173"
    log_slow_threshold_ms: float = Field(default=1000.0, ge=0)
    log_success_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    # duração do trabalho simulado pelo handler do case; 0 mede só a
    # maquinaria da fila (claim/finalize) nos benchmarks de throughput
    job_simulated_work_s: float = Field(default=1.0, ge=0)

    @field_validator("database_url")
    @classmethod
    def database_url_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL não pode ser vazia")
        return value

    @model_validator(mode="after")
    def pool_supports_processing(self) -> "Settings":
        if self.db_pool_size + self.db_max_overflow < 3:
            raise ValueError("o worker precisa de ao menos três conexões para processamento, heartbeat e cancelamento")
        return self


def _from_environ() -> Settings:
    raw = {name: os.environ[name.upper()] for name in Settings.model_fields if name.upper() in os.environ}
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env()
    return _from_environ()
