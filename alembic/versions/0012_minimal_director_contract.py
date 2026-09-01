"""极简混合导演契约与显式契约版本。

Revision ID: 0012_minimal_director_contract
Revises: 0011_narrative_render_core
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_minimal_director_contract"
down_revision: str | Sequence[str] | None = "0011_narrative_render_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"
CONTRACT_VERSION = 2


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def _assert_production_history_cleared() -> None:
    runs = sa.table("production_runs", sa.column("id", sa.Uuid()), schema=_schema())
    count = op.get_bind().execute(sa.select(sa.func.count()).select_from(runs)).scalar_one()
    if count:
        raise RuntimeError("0012不转换旧导演JSON；请先运行clear_production_history.py并保留Canon")


def upgrade() -> None:
    _assert_production_history_cleared()
    op.add_column(
        "production_runs",
        sa.Column(
            "contract_version",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text(str(CONTRACT_VERSION)),
        ),
        schema=_schema(),
    )
    op.create_check_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        f"contract_version = {CONTRACT_VERSION}",
        schema=_schema(),
    )


def downgrade() -> None:
    _assert_production_history_cleared()
    op.drop_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        type_="check",
        schema=_schema(),
    )
    op.drop_column("production_runs", "contract_version", schema=_schema())
