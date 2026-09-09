"""Store per-project environment drafts; historical jobs and assets remain immutable."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_environment_draft"
down_revision = "0031_job_execution_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("environment_generation_draft_json", postgresql.JSONB(), nullable=True), schema="catflow")


def downgrade() -> None:
    op.drop_column("projects", "environment_generation_draft_json", schema="catflow")
