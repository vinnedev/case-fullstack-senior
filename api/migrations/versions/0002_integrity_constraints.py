"""integrity constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint("ck_jobs_status", "jobs", "status IN ('queued', 'running', 'done', 'failed')")
    op.create_unique_constraint("uq_job_results_job_id", "job_results", ["job_id"])
    op.drop_index("ix_job_results_job_id", table_name="job_results")
    op.drop_constraint("job_results_job_id_fkey", "job_results", type_="foreignkey")
    op.create_foreign_key("job_results_job_id_fkey", "job_results", "jobs", ["job_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade():
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_constraint("job_results_job_id_fkey", "job_results", type_="foreignkey")
    op.create_foreign_key("job_results_job_id_fkey", "job_results", "jobs", ["job_id"], ["id"])
    op.create_index("ix_job_results_job_id", "job_results", ["job_id"])
    op.drop_constraint("uq_job_results_job_id", "job_results", type_="unique")
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
