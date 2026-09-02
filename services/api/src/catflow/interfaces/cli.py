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
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow.interfaces.api import AppSettings, create_app

app = typer.Typer(no_args_is_help=True)


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
        StudioService(repository, provider_runtime=provider_runtime),
        settings=AppSettings(
            csrf_token=secrets.token_urlsafe(32),
            base_url=runtime.base_url,
            allowed_origins=runtime.allowed_origins,
            ark_api_key_configured=bool(os.environ.get("ARK_API_KEY", "").strip()),
            worker_ready_file=paths.work_root / "worker-ready.json",
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
