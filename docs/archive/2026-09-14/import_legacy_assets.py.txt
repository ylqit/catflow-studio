from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

import psycopg
from dotenv import dotenv_values, load_dotenv
from psycopg import sql
from psycopg.rows import dict_row

from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    ensure_canon_v4,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import (
    AssetRecord,
    LifePlannerSessionRecord,
    ProjectRecord,
    ProjectSelectionRecord,
)

IMPORT_NAMESPACE = uuid.UUID("bce93c99-a589-4566-9197-2db834d7c30e")
PROJECT_ROLE_MAP = {
    "character_design_child": "episode_child",
    "character_design_cat": "episode_cat",
    "character_design_pair_scale": "pair_scale",
    "scene_look": "environment",
    "style_board": "style_board",
    "shot_video": "video",
    "project_sequence": "final",
}
CANON_ROLE_MAP = {
    "person": "canon_child",
    "cat": "canon_cat",
    "style": "canon_style_reference",
    "style_source": "style_source",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently import only approved legacy media into CatFlow."
    )
    parser.add_argument(
        "--legacy-env",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "cat-video-generator" / ".env",
    )
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=True)
    target_settings = DatabaseSettings.from_env()
    legacy = _legacy_settings(arguments.legacy_env)
    legacy_media_root = _legacy_media_root(arguments.legacy_env, legacy)
    schema = str(legacy.pop("schema"))

    with psycopg.connect(**legacy, row_factory=dict_row) as connection:
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        runs = connection.execute(
            """
            SELECT r.id, r.title, r.created_at,
                   COALESCE(b.theme, r.title) AS theme,
                   COALESCE(b.target_duration_seconds, 10) AS target_duration_seconds
            FROM production_runs r
            LEFT JOIN LATERAL (
                SELECT theme, target_duration_seconds
                FROM story_briefs
                WHERE production_run_id = r.id
                ORDER BY revision DESC
                LIMIT 1
            ) b ON true
            ORDER BY r.created_at
            """
        ).fetchall()
        approved_assets = connection.execute(
            """
            SELECT a.*,
                   EXISTS (
                       SELECT 1 FROM character_design_assets c
                       WHERE c.asset_id = a.id AND c.selected = true
                   ) AS explicitly_selected
            FROM assets a
            WHERE a.status = 'approved'
              AND a.role = ANY(%s)
            ORDER BY a.created_at
            """,
            (list(PROJECT_ROLE_MAP) + list(CANON_ROLE_MAP),),
        ).fetchall()

    project_assets = [row for row in approved_assets if row["role"] in PROJECT_ROLE_MAP]
    canon_assets = [row for row in approved_assets if row["role"] in CANON_ROLE_MAP]
    discovered_files = [
        row
        for row in approved_assets
        if _source_path(legacy_media_root, str(row["storage_key"])) is not None
    ]
    print(
        json.dumps(
            {
                "mode": "apply" if arguments.apply else "dry-run",
                "projects": len(runs),
                "approvedProjectAssets": len(project_assets),
                "approvedCanonAssets": len(canon_assets),
                "readableMediaFiles": len(discovered_files),
                "excludedLegacyTasks": True,
                "excludedCanvasAndReviews": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not arguments.apply:
        return

    engine = create_database_engine(target_settings)
    sessions = create_session_factory(engine)
    media_store = LocalMediaStore(project_root / "var" / "media")
    try:
        with sessions.begin() as session:
            canon = ensure_canon_v4(session)
            projects: dict[uuid.UUID, uuid.UUID] = {}
            for run in runs:
                legacy_id = uuid.UUID(str(run["id"]))
                project_id = uuid.uuid5(IMPORT_NAMESPACE, f"project:{legacy_id}")
                projects[legacy_id] = project_id
                if session.get(ProjectRecord, project_id) is None:
                    duration = max(8, min(15, int(run["target_duration_seconds"] or 10)))
                    session.add(
                        ProjectRecord(
                            id=project_id,
                            title=str(run["title"]),
                            theme=str(run["theme"]),
                            target_duration_seconds=duration,
                            aspect_ratio="9:16",
                            canon_profile_id=canon.id,
                            created_at=run["created_at"],
                            updated_at=run["created_at"],
                        )
                    )
                    session.add(
                        LifePlannerSessionRecord(
                            id=uuid.uuid5(IMPORT_NAMESPACE, f"planner:{legacy_id}"),
                            project_id=project_id,
                            context_revision=1,
                        )
                    )

            imported_by_slot: dict[tuple[uuid.UUID, str], list[tuple[dict, AssetRecord]]] = (
                defaultdict(list)
            )
            for row in project_assets:
                legacy_run_id = row["production_run_id"]
                if legacy_run_id is None or uuid.UUID(str(legacy_run_id)) not in projects:
                    continue
                project_id = projects[uuid.UUID(str(legacy_run_id))]
                mapped_role = PROJECT_ROLE_MAP[str(row["role"])]
                record = _import_asset(
                    session,
                    media_store,
                    legacy_media_root,
                    row,
                    role=mapped_role,
                    project_id=project_id,
                    canon_profile_id=None,
                )
                if record is not None:
                    imported_by_slot[(project_id, mapped_role)].append((row, record))

            for row in canon_assets:
                _import_asset(
                    session,
                    media_store,
                    legacy_media_root,
                    row,
                    role=CANON_ROLE_MAP[str(row["role"])],
                    project_id=None,
                    canon_profile_id=canon.id,
                )

            for (project_id, slot), candidates in imported_by_slot.items():
                selected_row, selected_asset = sorted(
                    candidates,
                    key=lambda item: (
                        bool(item[0]["explicitly_selected"]),
                        item[0]["created_at"],
                    ),
                )[-1]
                selection_id = uuid.uuid5(
                    IMPORT_NAMESPACE, f"selection:{selected_row['id']}:{slot}"
                )
                if session.get(ProjectSelectionRecord, selection_id) is None:
                    session.add(
                        ProjectSelectionRecord(
                            id=selection_id,
                            project_id=project_id,
                            asset_id=selected_asset.id,
                            slot=slot,
                            decision="approved" if slot == "final" else "selected",
                            source_hash=_selection_hash(project_id, slot, selected_asset.sha256),
                            reason="Imported from an approved legacy asset",
                            created_at=selected_row["created_at"],
                        )
                    )
        print("Approved legacy media import completed.")
    finally:
        engine.dispose()


def _legacy_settings(path: Path) -> dict[str, object]:
    values = dotenv_values(path)
    required = {
        "host": values.get("CAT_VIDEO_DB_HOST"),
        "port": int(values.get("CAT_VIDEO_DB_PORT") or 5432),
        "dbname": values.get("CAT_VIDEO_DB_NAME"),
        "user": values.get("CAT_VIDEO_DB_USER"),
        "password": values.get("CAT_VIDEO_DB_PASSWORD"),
        "sslmode": values.get("CAT_VIDEO_DB_SSLMODE") or "prefer",
        "schema": values.get("CAT_VIDEO_DB_SCHEMA") or "public",
    }
    if not all(required[key] for key in ("host", "dbname", "user", "password")):
        raise RuntimeError("legacy database configuration is incomplete")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(required["schema"])):
        raise RuntimeError("legacy schema name is invalid")
    return required


def _legacy_media_root(path: Path, legacy: dict[str, object]) -> Path:
    values = dotenv_values(path)
    configured = Path(str(values.get("MEDIA_ASSET_ROOT") or "var/media"))
    if not configured.is_absolute():
        configured = path.resolve().parent / configured
    return configured.resolve()


def _source_path(root: Path, storage_key: str) -> Path | None:
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _import_asset(
    session: object,
    media_store: LocalMediaStore,
    legacy_media_root: Path,
    row: dict,
    *,
    role: str,
    project_id: uuid.UUID | None,
    canon_profile_id: uuid.UUID | None,
) -> AssetRecord | None:
    source = _source_path(legacy_media_root, str(row["storage_key"]))
    if source is None:
        return None
    digest = _sha256(source)
    if digest != row["sha256"]:
        raise RuntimeError(f"legacy asset hash mismatch: {row['id']}")
    asset_id = uuid.uuid5(IMPORT_NAMESPACE, f"asset:{row['id']}")
    existing = session.get(AssetRecord, asset_id)
    if existing is not None:
        return existing
    extension = source.suffix.lower()
    storage_key = f"imports/legacy/{digest[:2]}/{digest}{extension}"
    destination = media_store.resolve(storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(destination.suffix + ".partial")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    metadata = dict(row["metadata_json"] or {})
    metadata.update(
        {
            "legacyAssetId": str(row["id"]),
            "legacyRole": row["role"],
            "legacyStatus": row["status"],
            "providerEligible": role != "style_source",
        }
    )
    record = AssetRecord(
        id=asset_id,
        project_id=project_id,
        canon_profile_id=canon_profile_id,
        role=role,
        media_type=row["media_type"],
        storage_key=storage_key,
        sha256=digest,
        byte_size=destination.stat().st_size,
        width=metadata.get("width"),
        height=metadata.get("height"),
        duration_ms=metadata.get("duration_ms") or metadata.get("durationMs"),
        metadata_json=metadata,
        created_at=row["created_at"],
    )
    session.add(record)
    return record


def _selection_hash(project_id: uuid.UUID, slot: str, sha256: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {"projectId": str(project_id), "slot": slot, "sha256": sha256},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
