from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base

JOB_STATUSES = ("queued", "running", "done", "failed")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="ck_jobs_status"),
        Index("ix_jobs_company_created", "company_id", "created_at"),
        Index("ix_jobs_queued", "id", postgresql_where="status = 'queued'"),
        Index("ix_jobs_company_active", "company_id", postgresql_where="status IN ('queued', 'running')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    result: Mapped["JobResult | None"] = relationship(back_populates="job")


class JobResult(Base):
    __tablename__ = "job_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    payload: Mapped[str] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="result")
