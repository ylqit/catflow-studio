"""Back up, rehearse and apply the LibTV canvas database migrations.

This runbook is intentionally locked to the explicitly approved production
target.  It keeps a database-resident report in the timestamped backup schema
and prints the same non-secret report for the operator.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from alembic import command
from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.domain.universal_canvas import CanvasTemplateKey
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    SqlAlchemyAigcCanvasRepository,
)
from cat_video_generator.infrastructure.db.models import ProductionRun
from cat_video_generator.infrastructure.db.session import create_database_engine

EXPECTED_DATABASE = "vedio-appdb"
EXPECTED_SCHEMA = "cat_video"
SOURCE_REVISION = "0018_v5_shot_assistance"
TARGET_REVISION = "0021_libtv_subject_assistant"
LOCK_NAME = "cat-video-generator:libtv-canvas-upgrade"
ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(connection: Connection, schema: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["schema"] = schema
    return config


def _direct_engine(url: URL, settings: DatabaseSettings, application_name: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "sslmode": settings.sslmode,
            "application_name": application_name,
        },
    )


def _quoted(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _schema_snapshot(
    connection: Connection,
    *,
    source_schema: str,
    backup_schema: str,
) -> dict[str, int]:
    source = _quoted(connection, source_schema)
    backup = _quoted(connection, backup_schema)
    connection.execute(text(f"CREATE SCHEMA {backup}"))
    tables = list(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=:schema AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            ),
            {"schema": source_schema},
        ).scalars()
    )
    counts: dict[str, int] = {}
    for table_name in tables:
        table = _quoted(connection, table_name)
        connection.execute(
            text(f"CREATE TABLE {backup}.{table} (LIKE {source}.{table} INCLUDING ALL)")
        )
        connection.execute(text(f"INSERT INTO {backup}.{table} SELECT * FROM {source}.{table}"))
        source_count = int(
            connection.execute(text(f"SELECT count(*) FROM {source}.{table}")).scalar_one()
        )
        backup_count = int(
            connection.execute(text(f"SELECT count(*) FROM {backup}.{table}")).scalar_one()
        )
        if source_count != backup_count:
            raise RuntimeError(f"backup row count mismatch for {table_name}")
        counts[table_name] = source_count
    project_ids = list(
        connection.execute(
            text(f"SELECT id::text FROM {source}.production_runs ORDER BY id")
        ).scalars()
    )
    connection.execute(
        text(
            f"CREATE TABLE {backup}.migration_manifest ("
            "captured_at timestamptz NOT NULL, source_schema text NOT NULL, "
            "source_revision text NOT NULL, table_counts jsonb NOT NULL, "
            "project_ids jsonb NOT NULL)"
        )
    )
    connection.execute(
        text(
            f"INSERT INTO {backup}.migration_manifest "
            "(captured_at,source_schema,source_revision,table_counts,project_ids) "
            "VALUES (now(),:source_schema,:source_revision,CAST(:counts AS jsonb),"
            "CAST(:project_ids AS jsonb))"
        ),
        {
            "source_schema": source_schema,
            "source_revision": SOURCE_REVISION,
            "counts": json.dumps(counts, sort_keys=True),
            "project_ids": json.dumps(project_ids),
        },
    )
    return counts


def _revision(connection: Connection, schema: str) -> str:
    qualified = f"{_quoted(connection, schema)}.alembic_version"
    return str(connection.execute(text(f"SELECT version_num FROM {qualified}")).scalar_one())


def _run_rehearsal(
    maintenance: Connection,
    *,
    settings: DatabaseSettings,
    rehearsal_database: str,
    expected_projects: int,
) -> dict[str, Any]:
    target = _quoted(maintenance, settings.database)
    rehearsal = _quoted(maintenance, rehearsal_database)
    maintenance.execute(
        text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=:database AND pid<>pg_backend_pid()"
        ),
        {"database": settings.database},
    )
    maintenance.execute(text(f"CREATE DATABASE {rehearsal} TEMPLATE {target}"))
    rehearsal_engine = _direct_engine(
        settings.url.set(database=rehearsal_database),
        settings,
        "cat-video-generator-migration-rehearsal",
    )
    try:
        with rehearsal_engine.connect() as connection:
            command.upgrade(_alembic_config(connection, settings.schema), "head")
            upgraded_revision = _revision(connection, settings.schema)
            projects = int(
                connection.execute(
                    text(f"SELECT count(*) FROM {settings.schema}.production_runs")
                ).scalar_one()
            )
            if upgraded_revision != TARGET_REVISION or projects != expected_projects:
                raise RuntimeError("rehearsal upgrade validation failed")
            connection.commit()
        sessions = sessionmaker(bind=rehearsal_engine, expire_on_commit=False)
        repository = SqlAlchemyAigcCanvasRepository(
            sessions,
            asset_root=Path(os.environ.get("MEDIA_ASSET_ROOT", "var/assets")),
        )
        with rehearsal_engine.connect() as connection:
            first_project_id = connection.execute(
                text(
                    f"SELECT id FROM {settings.schema}.production_runs ORDER BY created_at LIMIT 1"
                )
            ).scalar_one()
        canvas = repository.get_canvas(first_project_id)
        if canvas["projectId"] != str(first_project_id):
            raise RuntimeError("rehearsal application read validation failed")
        with rehearsal_engine.connect() as connection:
            command.downgrade(
                _alembic_config(connection, settings.schema),
                SOURCE_REVISION,
            )
            rolled_back_revision = _revision(connection, settings.schema)
            if rolled_back_revision != SOURCE_REVISION:
                raise RuntimeError("rehearsal rollback validation failed")
            connection.commit()
        return {
            "database": rehearsal_database,
            "upgradedRevision": upgraded_revision,
            "applicationRead": True,
            "rolledBackRevision": rolled_back_revision,
        }
    finally:
        rehearsal_engine.dispose()
        maintenance.execute(text(f"DROP DATABASE IF EXISTS {rehearsal} WITH (FORCE)"))


def _seed_capabilities(connection: Connection, settings: DatabaseSettings) -> None:
    image_model = os.environ["ARK_IMAGE_MODEL"]
    video_model = os.environ["ARK_VIDEO_MODEL"]
    configured_resolution = os.environ.get("ARK_VIDEO_RESOLUTION", "720p").lower()
    capabilities = (
        (
            image_model,
            "image",
            {
                "provider": "ark",
                "model": image_model,
                "modes": ["text_to_image", "all_reference"],
                "aspectRatios": ["16:9", "4:3", "1:1", "3:4", "9:16"],
                "resolutions": ["1080p"],
                "durations": [1],
                "candidateCounts": list(range(1, 9)),
                "audio": False,
                "maxReferenceImages": 9,
            },
        ),
        (
            video_model,
            "video",
            {
                "provider": "ark",
                "model": video_model,
                "modes": ["text_to_video", "image_to_video"],
                "aspectRatios": ["16:9", "9:16"],
                "resolutions": [configured_resolution],
                "durations": [8, 10, 12, 15],
                "candidateCounts": [1],
                "audio": True,
                "maxReferenceImages": 9,
            },
        ),
    )
    for model, media_kind, document in capabilities:
        connection.execute(
            text(
                f"INSERT INTO {settings.schema}.provider_capabilities "
                "(id,provider,model,media_kind,capabilities_json,active,updated_at) "
                "VALUES (:id,'ark',:model,:media_kind,CAST(:capabilities AS jsonb),true,now()) "
                "ON CONFLICT (provider,model,media_kind) DO UPDATE SET "
                "capabilities_json=EXCLUDED.capabilities_json,active=true,updated_at=now()"
            ),
            {
                "id": uuid.uuid4(),
                "model": model,
                "media_kind": media_kind,
                "capabilities": json.dumps(document, ensure_ascii=False),
            },
        )


def _create_acceptance_project(engine: Engine) -> tuple[str, dict[str, Any]]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    with sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title=f"LibTV 核心画布验收 {datetime.now().strftime('%Y%m%d-%H%M')}",
                content_date=date.today(),
                status="active",
                canvas_v2_enabled=True,
                canvas_template_key=CanvasTemplateKey.PRODUCT_AD.value,
                universal_canvas_enabled=True,
                product_ad_template_enabled=True,
                video_edit_v2_enabled=True,
            )
        )
    repository = SqlAlchemyAigcCanvasRepository(
        sessions,
        asset_root=Path(os.environ.get("MEDIA_ASSET_ROOT", "var/assets")),
    )
    template = repository.instantiate_template(
        project_id,
        SimpleNamespace(template_key=CanvasTemplateKey.PRODUCT_AD),
    )
    canvas = repository.get_canvas(project_id)
    feature_flags = canvas.get("featureFlags", {})
    if not canvas["canvasV2Enabled"] or not all(
        feature_flags[key]
        for key in ("UNIVERSAL_CANVAS", "PRODUCT_AD_TEMPLATE", "VIDEO_EDIT_V2")
    ):
        raise RuntimeError("acceptance project feature flags were not enabled")
    return str(project_id), {"template": template["templateKey"], "nodes": len(canvas["nodes"])}


def execute() -> dict[str, Any]:
    load_local_env()
    settings = DatabaseSettings.from_env()
    if settings.database != EXPECTED_DATABASE or settings.schema != EXPECTED_SCHEMA:
        raise RuntimeError("migration runbook refuses an unapproved database target")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_schema = f"backup_{stamp}"
    rehearsal_database = f"cat_video_rehearsal_{stamp}"
    report: dict[str, Any] = {
        "startedAt": datetime.now(UTC).isoformat(),
        "target": f"{settings.database}.{settings.schema}",
        "sourceRevision": SOURCE_REVISION,
        "targetRevision": TARGET_REVISION,
        "backupSchema": backup_schema,
    }
    source_engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
    maintenance_engine = _direct_engine(
        settings.url.set(database="postgres"),
        settings,
        "cat-video-generator-migration-lock",
    ).execution_options(isolation_level="AUTOCOMMIT")
    production_upgraded = False
    acceptance_project_id: str | None = None
    try:
        with maintenance_engine.connect() as maintenance:
            maintenance.execute(
                text("SELECT pg_advisory_lock(hashtext(:name))"), {"name": LOCK_NAME}
            )
            try:
                with source_engine.begin() as connection:
                    if _revision(connection, settings.schema) != SOURCE_REVISION:
                        raise RuntimeError("source revision changed; migration aborted")
                    active = int(
                        connection.execute(
                            text(
                                f"SELECT count(*) FROM {settings.schema}.workflow_steps "
                                "WHERE status IN ('pending','submitting','queued','running')"
                            )
                        ).scalar_one()
                    )
                    if active:
                        raise RuntimeError("active workflow steps prevent migration")
                    before_counts = _schema_snapshot(
                        connection,
                        source_schema=settings.schema,
                        backup_schema=backup_schema,
                    )
                source_engine.dispose()
                report["rehearsal"] = _run_rehearsal(
                    maintenance,
                    settings=settings,
                    rehearsal_database=rehearsal_database,
                    expected_projects=before_counts["production_runs"],
                )
                source_engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
                with source_engine.connect() as connection:
                    command.upgrade(_alembic_config(connection, settings.schema), "head")
                    if _revision(connection, settings.schema) != TARGET_REVISION:
                        raise RuntimeError("production revision validation failed")
                    production_upgraded = True
                    for table_name in ("production_runs", "assets", "prompt_records"):
                        after = int(
                            connection.execute(
                                text(f"SELECT count(*) FROM {settings.schema}.{table_name}")
                            ).scalar_one()
                        )
                        if after != before_counts[table_name]:
                            raise RuntimeError(f"production row count changed for {table_name}")
                    _seed_capabilities(connection, settings)
                    connection.commit()
                acceptance_project_id, acceptance = _create_acceptance_project(source_engine)
                report["acceptanceProjectId"] = acceptance_project_id
                report["acceptance"] = acceptance
                with source_engine.begin() as connection:
                    expected_tables = {
                        "subject_completion_runs",
                        "node_generation_configs",
                        "canvas_recovery_points",
                        "video_edit_recipes",
                        "media_generation_batches",
                    }
                    actual_tables = set(
                        connection.execute(
                            text(
                                "SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema=:schema"
                            ),
                            {"schema": settings.schema},
                        ).scalars()
                    )
                    if not expected_tables.issubset(actual_tables):
                        raise RuntimeError("production schema table validation failed")
                    report["completedAt"] = datetime.now(UTC).isoformat()
                    report["projectsBefore"] = before_counts["production_runs"]
                    report["projectsAfter"] = before_counts["production_runs"] + 1
                    report["status"] = "completed"
                    backup = _quoted(connection, backup_schema)
                    connection.execute(
                        text(
                            f"CREATE TABLE {backup}.migration_result ("
                            "completed_at timestamptz NOT NULL, report_json jsonb NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            f"INSERT INTO {backup}.migration_result VALUES "
                            "(now(),CAST(:report AS jsonb))"
                        ),
                        {"report": json.dumps(report, ensure_ascii=False)},
                    )
            except Exception:
                if production_upgraded:
                    if acceptance_project_id is not None:
                        with source_engine.begin() as connection:
                            connection.execute(
                                text(
                                    f"DELETE FROM {settings.schema}.production_runs "
                                    "WHERE id=:project_id"
                                ),
                                {"project_id": uuid.UUID(acceptance_project_id)},
                            )
                    with source_engine.connect() as connection:
                        command.downgrade(
                            _alembic_config(connection, settings.schema),
                            SOURCE_REVISION,
                        )
                        connection.commit()
                raise
            finally:
                maintenance.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": LOCK_NAME}
                )
    finally:
        source_engine.dispose()
        maintenance_engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the explicitly approved production backup and migration",
    )
    arguments = parser.parse_args()
    if not arguments.execute:
        parser.error("--execute is required; dry inspection is handled by the database doctor")
    try:
        print(json.dumps(execute(), ensure_ascii=False, indent=2, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise


if __name__ == "__main__":
    main()
