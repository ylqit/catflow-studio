"""Add project collections, tags, pinning and recoverable archiving.

Revision ID: 0017_project_library
Revises: 0016_unproduced_asset_dedup
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_project_library"
down_revision = "0016_unproduced_asset_dedup"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "project_collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("normalized_name", sa.String(length=40), nullable=False),
        sa.Column("color_key", sa.String(length=16), nullable=False, server_default="clay"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_project_collections_active_name",
        "project_collections",
        ["normalized_name"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "ix_project_collections_sort",
        "project_collections",
        ["sort_order", "name"],
        schema=SCHEMA,
    )
    op.add_column(
        "projects",
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "projects", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "projects",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_projects_collection_id",
        "projects",
        "project_collections",
        ["collection_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index("ix_projects_collection_id", "projects", ["collection_id"], schema=SCHEMA)
    op.create_index("ix_projects_pinned_at", "projects", ["pinned_at"], schema=SCHEMA)
    op.create_index("ix_projects_archived_at", "projects", ["archived_at"], schema=SCHEMA)
    op.create_table(
        "project_tags",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=24), nullable=False),
        sa.Column("normalized_name", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "normalized_name"),
        sa.UniqueConstraint(
            "project_id", "normalized_name", name="uq_project_tags_project_normalized"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_project_tags_normalized",
        "project_tags",
        ["normalized_name"],
        schema=SCHEMA,
    )
    op.execute(
        """
        INSERT INTO catflow.project_tags (project_id, name, normalized_name)
        SELECT
            id,
            regexp_replace(normalize(btrim(theme), NFKC), '\\s+', ' ', 'g'),
            lower(regexp_replace(normalize(btrim(theme), NFKC), '\\s+', ' ', 'g'))
        FROM catflow.projects
        WHERE char_length(
            regexp_replace(normalize(btrim(theme), NFKC), '\\s+', ' ', 'g')
        ) BETWEEN 1 AND 24
        ON CONFLICT (project_id, normalized_name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("project_tags", schema=SCHEMA)
    op.drop_index("ix_projects_archived_at", table_name="projects", schema=SCHEMA)
    op.drop_index("ix_projects_pinned_at", table_name="projects", schema=SCHEMA)
    op.drop_index("ix_projects_collection_id", table_name="projects", schema=SCHEMA)
    op.drop_constraint("fk_projects_collection_id", "projects", schema=SCHEMA, type_="foreignkey")
    op.drop_column("projects", "archived_at", schema=SCHEMA)
    op.drop_column("projects", "pinned_at", schema=SCHEMA)
    op.drop_column("projects", "collection_id", schema=SCHEMA)
    op.drop_table("project_collections", schema=SCHEMA)
