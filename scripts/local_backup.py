from __future__ import annotations

import argparse
import json
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import DateTime, select
from sqlalchemy.dialects.postgresql import UUID

from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
)
from catflow.infrastructure.models import Base

TABLE_ORDER = (
    "canon_profiles",
    "projects",
    "life_planner_sessions",
    "life_planner_messages",
    "life_planner_proposals",
    "story_versions",
    "shot_plan_versions",
    "jobs",
    "assets",
    "project_selections",
    "job_events",
    "edit_versions",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("backup", "restore"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--replace", action="store_true")
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=True)
    settings = DatabaseSettings.from_env()
    if inspect_database_name(settings.url) != "catflow_studio":
        raise RuntimeError("backup and restore are restricted to the catflow_studio database")
    engine = create_database_engine(settings)
    try:
        if arguments.mode == "backup":
            backup(engine, project_root, arguments.archive)
        else:
            restore(engine, project_root, arguments.archive, replace=arguments.replace)
    finally:
        engine.dispose()


def inspect_database_name(url: str) -> str | None:
    from sqlalchemy import make_url

    return make_url(url).database


def backup(engine: Any, project_root: Path, archive: Path) -> None:
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, list[dict[str, object]]] = {}
    with engine.connect() as connection:
        for name in TABLE_ORDER:
            table = Base.metadata.tables[f"catflow.{name}"]
            rows = connection.execute(select(table)).mappings().all()
            document[name] = [{key: _encode(value) for key, value in row.items()} for row in rows]
    media_root = project_root / "var" / "media"
    temporary = archive.with_suffix(archive.suffix + ".partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "catflow-local-backup-v1",
                    "createdAt": datetime.now().astimezone().isoformat(),
                    "tables": list(TABLE_ORDER),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        bundle.writestr("database.json", json.dumps(document, ensure_ascii=False, indent=2))
        if media_root.is_dir():
            for path in media_root.rglob("*"):
                if path.is_file():
                    bundle.write(path, Path("media") / path.relative_to(media_root))
    temporary.replace(archive)
    print(f"Backup created: {archive}")


def restore(engine: Any, project_root: Path, archive: Path, *, replace: bool) -> None:
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        if manifest.get("format") != "catflow-local-backup-v1":
            raise RuntimeError("unsupported CatFlow backup format")
        document = json.loads(bundle.read("database.json"))
        with engine.begin() as connection:
            populated = any(
                connection.execute(select(Base.metadata.tables[f"catflow.{name}"]).limit(1)).first()
                is not None
                for name in TABLE_ORDER
            )
            if populated and not replace:
                raise RuntimeError(
                    "target database is not empty; rerun with --replace only after "
                    "confirming the backup"
                )
            if replace:
                for name in reversed(TABLE_ORDER):
                    connection.execute(Base.metadata.tables[f"catflow.{name}"].delete())

            supersedes: list[tuple[uuid.UUID, uuid.UUID]] = []
            for name in TABLE_ORDER:
                table = Base.metadata.tables[f"catflow.{name}"]
                rows = [_decode_row(table, row) for row in document[name]]
                if name == "jobs":
                    for row in rows:
                        if row.get("supersedes_job_id") is not None:
                            supersedes.append((row["id"], row["supersedes_job_id"]))
                            row["supersedes_job_id"] = None
                if rows:
                    connection.execute(table.insert(), rows)
            jobs = Base.metadata.tables["catflow.jobs"]
            for job_id, supersedes_job_id in supersedes:
                connection.execute(
                    jobs.update()
                    .where(jobs.c.id == job_id)
                    .values(supersedes_job_id=supersedes_job_id)
                )
            if document["job_events"]:
                maximum_event_id = max(row["id"] for row in document["job_events"])
                connection.exec_driver_sql(
                    "SELECT setval(pg_get_serial_sequence('catflow.job_events', 'id'), %s, true)",
                    (maximum_event_id,),
                )

        media_root = project_root / "var" / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        for member in bundle.infolist():
            if member.is_dir() or not member.filename.startswith("media/"):
                continue
            relative = Path(member.filename).relative_to("media")
            destination = (media_root / relative).resolve()
            if not destination.is_relative_to(media_root.resolve()):
                raise RuntimeError("backup contains an unsafe media path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, destination.open("wb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
    print(f"Backup restored: {archive}")


def _encode(value: object) -> object:
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    return value


def _decode_row(table: Any, row: dict[str, object]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for column in table.columns:
        value = row.get(column.name)
        if value is None:
            decoded[column.name] = None
        elif isinstance(column.type, UUID):
            decoded[column.name] = uuid.UUID(str(value))
        elif isinstance(column.type, DateTime):
            decoded[column.name] = datetime.fromisoformat(str(value))
        else:
            decoded[column.name] = value
    return decoded


if __name__ == "__main__":
    main()
