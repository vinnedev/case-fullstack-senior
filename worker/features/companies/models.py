from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db.base import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    max_concurrent_jobs: Mapped[int] = mapped_column(default=2)
    job_quota: Mapped[int] = mapped_column(default=100)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text, default="user")
