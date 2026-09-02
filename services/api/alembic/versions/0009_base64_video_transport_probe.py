"""Allow the one-shot Ark Base64 reference-video transport probe.

Revision ID: 0009_base64_video_probe
Revises: 0008_validation_repair_snapshot
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_base64_video_probe"
down_revision: str | Sequence[str] | None = "0008_validation_repair_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"
ORIGINAL_KINDS = (
    "plan_story",
    "generate_image",
    "diagnose_image",
    "generate_video",
    "diagnose_video",
    "regenerate_video_segment",
    "render_export",
)
PROBE_KINDS = (*ORIGINAL_KINDS[:-2], "probe_segment_video_data_url", *ORIGINAL_KINDS[-2:])


def _kind_constraint(kinds: tuple[str, ...]) -> sa.CheckConstraint:
    allowed = ",".join(f"'{item}'" for item in kinds)
    return sa.CheckConstraint(f"kind IN ({allowed})", name="ck_jobs_kind")


def upgrade() -> None:
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        _kind_constraint(PROBE_KINDS).sqltext,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        _kind_constraint(ORIGINAL_KINDS).sqltext,
        schema=SCHEMA,
    )
