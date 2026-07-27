from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from modules.jobs.models import JOB_STATUSES

JobStatus = str


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str = Field(examples=list(JOB_STATUSES))
    created_at: datetime
    result_count: int = Field(ge=0)


class JobDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    kind: str
    status: str


class JobResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payload: str


class NewJob(BaseModel):
    kind: str = Field(min_length=1, max_length=100, description="Tipo do job a processar", examples=["report"])


class JobCreated(BaseModel):
    id: int
    status: str = Field(examples=["queued"])


class AdminJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    status: str
