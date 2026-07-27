"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer, nullable=False, server_default="2"),
        sa.Column("job_quota", sa.Integer, nullable=False, server_default="100"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="user"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_jobs_company_created", "jobs", ["company_id", "created_at"])
    op.create_index("ix_jobs_queued", "jobs", ["id"], postgresql_where=sa.text("status = 'queued'"))
    op.create_index("ix_jobs_company_active", "jobs", ["company_id"], postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table(
        "job_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
    )
    op.create_index("ix_job_results_job_id", "job_results", ["job_id"])


def downgrade():
    op.drop_index("ix_job_results_job_id", table_name="job_results")
    op.drop_table("job_results")
    op.drop_index("ix_jobs_company_active", table_name="jobs")
    op.drop_index("ix_jobs_queued", table_name="jobs")
    op.drop_index("ix_jobs_company_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("users")
    op.drop_table("companies")
