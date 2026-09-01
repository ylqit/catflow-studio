"""Freeze ordered generation references and preserve honest lineage evidence.

Revision ID: 0030_generation_reference_lineage
Revises: 0029_storyboard_generation_plans
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030_generation_reference_lineage"
down_revision: str | Sequence[str] | None = "0029_storyboard_generation_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _legacy_input_hash(input_json: Any) -> str:
    """Return only an input hash the legacy task actually persisted.

    Re-hashing a reconstructed manifest would create a new value that cannot prove
    the Provider request seen by the historical Worker.  Empty means exactly that:
    the old task has selection evidence, but no trustworthy frozen input hash.
    """

    document = input_json if isinstance(input_json, dict) else {}
    config = document.get("generationConfig")
    candidates = (
        document.get("inputHash"),
        document.get("referenceManifestHash"),
        config.get("inputHash") if isinstance(config, dict) else None,
        config.get("referenceManifestHash") if isinstance(config, dict) else None,
    )
    for candidate in candidates:
        value = str(candidate or "").strip().lower()
        if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
            return value
    return ""


def _legacy_manifest(input_json: Any, media_kind: str) -> list[dict[str, Any]]:
    document = input_json if isinstance(input_json, dict) else {}
    config = document.get("generationConfig")
    actual = config.get("actualReferences") if isinstance(config, dict) else None
    manifest: list[dict[str, Any]] = []
    if isinstance(actual, list):
        included = [item for item in actual if isinstance(item, dict)]
        for ordinal, item in enumerate(included, 1):
            asset_id = item.get("assetId")
            if not asset_id:
                continue
            manifest.append(
                {
                    "assetId": str(asset_id),
                    "sourceNodeId": item.get("sourceNodeId"),
                    "sourceType": item.get("sourceType") or "legacy_canvas",
                    "subjectRevisionId": item.get("subjectRevisionId"),
                    "semanticRole": item.get("semanticRole") or "reference",
                    "purpose": item.get("purpose") or "reference",
                    "instruction": item.get("instruction") or "",
                    "ordinal": ordinal,
                    "locked": bool(item.get("locked", False)),
                    "sha256": item.get("sha256"),
                    "providerIncluded": bool(item.get("providerIncluded", True)),
                    "providerSlot": item.get("providerSlot"),
                    "omissionReason": item.get("omissionReason"),
                    "origin": item.get("origin") or "legacy_generation_config",
                    # The legacy generation config proves selection intent, not the
                    # Worker-to-Provider slot order.  Both image and video batches
                    # therefore remain selected-only unless an exact provider
                    # manifest was already persisted by the new write path.
                    "evidenceLevel": "selected_only",
                }
            )
        return manifest
    selected = document.get("referenceAssetIds")
    if not isinstance(selected, list):
        return []
    for ordinal, asset_id in enumerate(selected, 1):
        manifest.append(
            {
                "assetId": str(asset_id),
                "sourceType": "legacy_batch_input",
                "semanticRole": "reference",
                "purpose": "reference",
                "instruction": "",
                "ordinal": ordinal,
                "locked": False,
                "sha256": None,
                "providerIncluded": True,
                "providerSlot": None,
                "omissionReason": None,
                "origin": "legacy_referenceAssetIds",
                "evidenceLevel": "unknown" if media_kind == "image" else "selected_only",
            }
        )
    return manifest


def _legacy_storyboard_bindings(row: Any) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = [
        {
            "storyRevisionId": str(row["story_revision_id"]),
            "sourceType": "approved_story",
            "semanticRole": "story_source",
            "purpose": "storyboard_structure",
            "instruction": "已批准剧情脚本是该分镜版本的文本来源",
            "ordinal": 1,
            "locked": True,
            "evidenceLevel": "frozen",
        }
    ]
    snapshots = (
        row.get("prompt_input_snapshot"),
        row.get("step_input_snapshot"),
        row.get("provider_request_snapshot"),
    )
    candidate_keys = (
        "inputBindings",
        "characterReferenceBindings",
        "referenceBindings",
        "references",
    )
    seen_assets: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for key in candidate_keys:
            raw = snapshot.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict) or not item.get("assetId"):
                    continue
                asset_id = str(item["assetId"])
                if asset_id in seen_assets:
                    continue
                seen_assets.add(asset_id)
                bindings.append(
                    {
                        "assetId": asset_id,
                        "sha256": item.get("sha256"),
                        "sourceType": item.get("sourceType") or "storyboard_source_snapshot",
                        "semanticRole": item.get("semanticRole") or item.get("role") or "reference",
                        "purpose": item.get("purpose") or "director_reference",
                        "instruction": item.get("instruction") or "历史分镜任务保存的明确输入绑定",
                        "ordinal": len(bindings) + 1,
                        "locked": bool(item.get("locked", False)),
                        "evidenceLevel": "frozen" if item.get("sha256") else "selected_only",
                    }
                )
    return bindings


def upgrade() -> None:
    schema = _schema()
    # Alembic creates this bookkeeping column as VARCHAR(32) by default.  The
    # descriptive revision IDs introduced at 0030 exceed that legacy capacity,
    # so widen it before Alembic records this revision at transaction commit.
    # Downgrades intentionally keep the wider, backward-compatible capacity.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
        schema=schema,
    )
    op.add_column(
        "storyboard_revisions",
        sa.Column(
            "input_bindings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=schema,
    )
    op.add_column(
        "shot_beats",
        sa.Column(
            "reference_bindings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=schema,
    )
    op.add_column(
        "shot_beats",
        sa.Column("reference_binding_revision", sa.Integer(), server_default="1", nullable=False),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_shot_beats_reference_binding_revision",
        "shot_beats",
        "reference_binding_revision >= 1",
        schema=schema,
    )
    op.add_column(
        "media_generation_batches",
        sa.Column(
            "reference_manifest_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=schema,
    )
    op.add_column(
        "media_generation_batches",
        sa.Column(
            "reference_manifest_hash",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        schema=schema,
    )

    connection = op.get_bind()
    storyboard_rows = connection.execute(
        sa.text(
            f"""
            SELECT sr.id,
                   sr.story_revision_id,
                   ws.input_snapshot_json AS step_input_snapshot,
                   pr.input_snapshot_json AS prompt_input_snapshot,
                   pr.provider_request_json AS provider_request_snapshot
            FROM {schema}.storyboard_revisions sr
            LEFT JOIN {schema}.workflow_steps ws ON ws.id = sr.source_step_id
            LEFT JOIN LATERAL (
                SELECT candidate.input_snapshot_json,
                       candidate.provider_request_json
                FROM {schema}.prompt_records candidate
                WHERE candidate.step_id = sr.source_step_id
                ORDER BY candidate.created_at DESC, candidate.id DESC
                LIMIT 1
            ) pr ON TRUE
            """
        )
    ).mappings()
    for row in storyboard_rows:
        bindings = _legacy_storyboard_bindings(row)
        connection.execute(
            sa.text(
                f"""
                UPDATE {schema}.storyboard_revisions
                SET input_bindings_json = CAST(:bindings AS jsonb)
                WHERE id = :storyboard_id
                """
            ),
            {
                "storyboard_id": row["id"],
                "bindings": json.dumps(bindings, ensure_ascii=False),
            },
        )
    rows = connection.execute(
        sa.text(
            f"SELECT id, media_kind, input_json FROM {schema}.media_generation_batches"
        )
    ).mappings()
    for row in rows:
        manifest = _legacy_manifest(row["input_json"], str(row["media_kind"]))
        connection.execute(
            sa.text(
                f"""
                UPDATE {schema}.media_generation_batches
                SET reference_manifest_json = CAST(:manifest AS jsonb),
                    reference_manifest_hash = :manifest_hash
                WHERE id = :batch_id
                """
            ),
            {
                "batch_id": row["id"],
                "manifest": json.dumps(manifest, ensure_ascii=False),
                "manifest_hash": _legacy_input_hash(row["input_json"]),
            },
        )


def downgrade() -> None:
    schema = _schema()
    op.drop_column("media_generation_batches", "reference_manifest_hash", schema=schema)
    op.drop_column("media_generation_batches", "reference_manifest_json", schema=schema)
    op.drop_constraint(
        "ck_shot_beats_reference_binding_revision",
        "shot_beats",
        schema=schema,
        type_="check",
    )
    op.drop_column("shot_beats", "reference_binding_revision", schema=schema)
    op.drop_column("shot_beats", "reference_bindings_json", schema=schema)
    op.drop_column("storyboard_revisions", "input_bindings_json", schema=schema)
