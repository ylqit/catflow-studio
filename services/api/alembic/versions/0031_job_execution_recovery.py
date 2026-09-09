"""Add recoverable execution facts without rewriting historical requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_job_execution_recovery"
down_revision = "0030_candidate_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("provider_response_id", sa.String(200), nullable=True),
        sa.Column("provider_client_request_id", sa.String(200), nullable=True),
        sa.Column("execution_json", postgresql.JSONB(), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("jobs", column, schema="catflow")
    op.create_index("ix_jobs_response", "jobs", ["provider", "provider_response_id"], schema="catflow")
    op.create_index("ix_jobs_next_action", "jobs", ["next_action_at", "status"], schema="catflow")
    op.create_index("ix_job_events_job_id", "job_events", ["job_id", "id"], schema="catflow")


def downgrade() -> None:
    raise RuntimeError("Execution receipts and recovery history must be retained.")
