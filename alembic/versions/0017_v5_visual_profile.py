"""Add versioned project visual profiles and scene look drafts.

Revision ID: 0017_v5_visual_profile
Revises: 0016_v5_creation_flow
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_v5_visual_profile"
down_revision: str | Sequence[str] | None = "0016_v5_creation_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_PROFILE_ID = "canon-v1-short-hair-gray-cat-sample-style"
_PERSON_IDENTITY = (
    "同一个偏中性呈现的东亚儿童，保持批准人物正面图中的柔和椭圆脸、五官比例、"
    "肤色和自然儿童年龄感，不强化男性或女性特征"
)
_PERSON_HAIR = (
    "保持深棕黑色、齐耳至下颌长度的顺直短波波头与轻薄刘海，不得无故变成长发、马尾或发髻"
)
_PERSON_BODY = "保持约五至七岁儿童的身高感、头身比例和纤细自然体型，可由剧情自然换装"
_CAT_IDENTITY = (
    "同一只圆润灰白短毛猫，保持白色口鼻胸腹与四肢、灰色头顶和背部虎斑、"
    "灰白环纹、自然中等粗细且从后躯正常连接的尾巴、圆形琥珀棕眼睛及稳定体型"
)
_STYLE_POSITIVE = [
    "日系二维治愈生活插画",
    "细腻干净的手绘轮廓线",
    "柔和哑光的水彩式数字绘制",
    "清新低至中饱和自然色",
    "温和自然光与空气透视",
    "克制景深和轻微远景虚化",
]
_STYLE_NEGATIVE = [
    "真人写实摄影",
    "CG或PBR三维材质",
    "塑料高光",
    "强烈3D体积塑形",
    "过度油亮平滑表面",
    "高反差商业动画灯光",
    "光滑商业动画渲染",
]


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "visual_profile_revisions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "production_run_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("source_profile_id", sa.String(80), nullable=False),
        sa.Column("person_identity", sa.Text(), nullable=False),
        sa.Column("person_hair", sa.Text(), nullable=False),
        sa.Column("person_body", sa.Text(), nullable=False),
        sa.Column("cat_identity", sa.Text(), nullable=False),
        sa.Column("style_positive_json", postgresql.JSONB(), nullable=False),
        sa.Column("style_negative_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "reference_bindings_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "reference_snapshot_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name="ck_visual_profile_revisions_revision"),
        sa.UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_visual_profile_revisions_run_revision",
        ),
        sa.UniqueConstraint(
            "production_run_id",
            "profile_hash",
            name="uq_visual_profile_revisions_run_hash",
        ),
        schema=schema,
    )
    op.add_column(
        "production_runs",
        sa.Column("current_visual_profile_revision_id", uuid_type, nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_production_runs_visual_profile_revision",
        "production_runs",
        "visual_profile_revisions",
        ["current_visual_profile_revision_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.add_column(
        "scenes",
        sa.Column(
            "look_draft_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=schema,
    )
    op.add_column(
        "scenes",
        sa.Column("look_draft_revision", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    _backfill_profiles(schema)


def _backfill_profiles(schema: str) -> None:
    bind = op.get_bind()
    canon_assets = {
        str(row["id"]): {
            "semanticKey": str(row["semantic_key"]),
            "sha256": str(row["sha256"]),
        }
        for row in bind.execute(
            sa.text(
                f"SELECT id, semantic_key, sha256 FROM {schema}.assets "
                "WHERE scope = 'canon' AND semantic_key IS NOT NULL"
            )
        ).mappings()
    }
    runs = bind.execute(
        sa.text(
            f"SELECT id, default_reference_bindings_json FROM {schema}.production_runs"
        )
    ).mappings()
    for row in runs:
        references = _look_references(row["default_reference_bindings_json"], canon_assets)
        reference_snapshot = [
            {
                **item,
                "semanticKey": canon_assets[item["assetId"]]["semanticKey"],
                "sha256": canon_assets[item["assetId"]]["sha256"],
            }
            for item in references
        ]
        profile = {
            "personIdentity": _PERSON_IDENTITY,
            "personHair": _PERSON_HAIR,
            "personBody": _PERSON_BODY,
            "catIdentity": _CAT_IDENTITY,
            "stylePositive": _STYLE_POSITIVE,
            "styleNegative": _STYLE_NEGATIVE,
            "referenceBindings": references,
            "referenceSnapshot": reference_snapshot,
        }
        profile_hash = hashlib.sha256(
            json.dumps(profile, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        profile_id = uuid.uuid4()
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {schema}.visual_profile_revisions (
                    id, production_run_id, revision, profile_hash, source_profile_id,
                    person_identity, person_hair, person_body, cat_identity,
                    style_positive_json, style_negative_json, reference_bindings_json,
                    reference_snapshot_json
                ) VALUES (
                    :id, :project_id, 1, :profile_hash, :source_profile_id,
                    :person_identity, :person_hair, :person_body, :cat_identity,
                    CAST(:style_positive AS jsonb), CAST(:style_negative AS jsonb),
                    CAST(:references AS jsonb), CAST(:reference_snapshot AS jsonb)
                )
                """
            ),
            {
                "id": profile_id,
                "project_id": row["id"],
                "profile_hash": profile_hash,
                "source_profile_id": _SOURCE_PROFILE_ID,
                "person_identity": _PERSON_IDENTITY,
                "person_hair": _PERSON_HAIR,
                "person_body": _PERSON_BODY,
                "cat_identity": _CAT_IDENTITY,
                "style_positive": json.dumps(_STYLE_POSITIVE, ensure_ascii=False),
                "style_negative": json.dumps(_STYLE_NEGATIVE, ensure_ascii=False),
                "references": json.dumps(references, ensure_ascii=False),
                "reference_snapshot": json.dumps(reference_snapshot, ensure_ascii=False),
            },
        )
        bind.execute(
            sa.text(
                f"UPDATE {schema}.production_runs "
                "SET current_visual_profile_revision_id = :profile_id WHERE id = :project_id"
            ),
            {"profile_id": profile_id, "project_id": row["id"]},
        )


def _look_references(
    existing: object,
    semantic_keys: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    if not isinstance(existing, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in existing:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("assetId", ""))
        asset_info = semantic_keys.get(asset_id, {})
        semantic_key = asset_info.get("semanticKey", "")
        if not asset_id or asset_id in seen:
            continue
        if semantic_key == "person:headshot":
            purpose = "person_identity"
        elif semantic_key.startswith("person:"):
            purpose = "person_body"
        elif semantic_key.startswith("cat:"):
            purpose = "cat_identity"
        elif semantic_key.startswith("style:"):
            purpose = "style"
        else:
            continue
        seen.add(asset_id)
        result.append({"assetId": asset_id, "purpose": purpose, "instruction": ""})
    return result


def downgrade() -> None:
    raise RuntimeError("V5 visual profile revisions cannot be downgraded safely")
