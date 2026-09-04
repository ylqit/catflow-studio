from __future__ import annotations

import os
import secrets
from pathlib import Path

import typer
import uvicorn
from dotenv import load_dotenv

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import StudioService
from catflow.config import RuntimeConfig, RuntimePaths
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.object_storage import ObjectPublisherRuntime
from catflow.infrastructure.postgres_project_library import PostgresProjectLibraryRepository
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow.interfaces.api import AppSettings, create_app
from catflow.maintenance.cleanup import CleanupService

app = typer.Typer(no_args_is_help=True)
cleanup_app = typer.Typer(no_args_is_help=True, help="Audit and execute reviewed data cleanup.")
app.add_typer(cleanup_app, name="cleanup")


def validate_loopback_host(host: str) -> str:
    return RuntimeConfig(host=host).host


@app.command()
def serve(
    port: int | None = typer.Option(None, min=1024, max=65535),
) -> None:
    """Serve the CatFlow API and built Vue application on the loopback interface."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    configured = RuntimeConfig.from_env()
    runtime = RuntimeConfig(port=port or configured.port)
    object_publisher_runtime = ObjectPublisherRuntime.from_env()
    provider_runtime = ProviderRuntime.from_env(
        segment_reference_publishing_ready=object_publisher_runtime.status.ready
    )

    spa_dist = project_root / "apps" / "web" / "dist"
    if not (spa_dist / "index.html").is_file():
        raise typer.BadParameter(
            "apps/web/dist is missing; run npm --workspace apps/web run build first"
        )

    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    repository.active_canon_profile_id()
    application = create_app(
        StudioService(
            repository,
            provider_runtime=provider_runtime,
            project_library_repository=PostgresProjectLibraryRepository(sessions),
        ),
        settings=AppSettings(
            csrf_token=secrets.token_urlsafe(32),
            base_url=runtime.base_url,
            allowed_origins=runtime.allowed_origins,
            ark_api_key_configured=bool(os.environ.get("ARK_API_KEY", "").strip()),
            worker_ready_file=paths.work_root / "worker-ready.json",
            worker_supervisor_file=paths.work_root / "worker-supervisor.json",
            ffmpeg_ready=_configured_tool_ready("FFMPEG_PATH"),
            ffprobe_ready=_configured_tool_ready("FFPROBE_PATH"),
        ),
        media_store=LocalMediaStore(paths.media_root),
        spa_dist=spa_dist,
        object_publisher_runtime=object_publisher_runtime,
    )
    application.state.database_engine = engine
    uvicorn.run(application, host=runtime.host, port=runtime.port)


def _configured_tool_ready(environment_name: str) -> bool:
    value = os.environ.get(environment_name, "")
    return bool(value and Path(value).is_file())


@cleanup_app.command("audit")
def cleanup_audit(
    output: Path = typer.Option(..., dir_okay=False),
) -> None:
    """Create a read-only, hashed cleanup manifest."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    engine = create_database_engine(DatabaseSettings.from_env())
    try:
        document = CleanupService(create_session_factory(engine), paths).audit(output)
        typer.echo(f"Cleanup manifest: {output.resolve()}")
        typer.echo(f"Manifest SHA256: {document['manifestSha256']}")
    finally:
        engine.dispose()


@cleanup_app.command("execute")
def cleanup_execute(
    manifest: Path = typer.Option(..., exists=True, dir_okay=False),
    manifest_sha256: str = typer.Option(..., "--manifest-sha256"),
) -> None:
    """Back up, quarantine, and transactionally execute a reviewed manifest."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    engine = create_database_engine(DatabaseSettings.from_env())
    try:
        run_root = CleanupService(create_session_factory(engine), paths).execute(
            manifest, manifest_sha256
        )
        typer.echo(f"Cleanup completed: {run_root}")
    finally:
        engine.dispose()


@cleanup_app.command("restore")
def cleanup_restore(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Back up the current state, then restore a completed cleanup backup."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    engine = create_database_engine(DatabaseSettings.from_env())
    try:
        CleanupService(create_session_factory(engine), paths).restore(run_id)
        typer.echo(f"Cleanup run restored: {run_id}")
    finally:
        engine.dispose()


@cleanup_app.command("purge-quarantine")
def cleanup_purge_quarantine(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Purge exact quarantined files after the seven-day reference recheck."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    engine = create_database_engine(DatabaseSettings.from_env())
    try:
        removed = CleanupService(create_session_factory(engine), paths).purge_quarantine(run_id)
        typer.echo(f"Purged {removed} quarantined files for cleanup run {run_id}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    app()
