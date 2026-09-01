from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import typer
from dotenv import load_dotenv

from catflow.application.service import StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.postgres_repository import PostgresStudioRepository

from .fake_provider import FakeProviderGateway
from .media_jobs import MediaJobExecutor
from .runner import DurableJobWorker

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run_worker(
    once: bool = typer.Option(False, help="Process at most one available lifecycle step."),
    poll_interval: float = typer.Option(1.0, min=0.1, max=30),
) -> None:
    """Run the durable planning, fake-media and FFmpeg worker."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=True)
    provider_name = os.environ.get("CATFLOW_PROVIDER", "fake")
    if provider_name != "fake":
        raise typer.BadParameter(
            "Only the zero-cost fake provider is enabled in this implementation."
        )
    ffmpeg_path = _required_tool("FFMPEG_PATH")
    ffprobe_path = _required_tool("FFPROBE_PATH")
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    media_store = LocalMediaStore(project_root / os.environ.get("CATFLOW_MEDIA_ROOT", "var/media"))
    worker = DurableJobWorker(
        sessions,
        FakeProviderGateway(),
        worker_id=f"{socket.gethostname()}-{os.getpid()}",
        studio_service=service,
        result_handler=MediaJobExecutor(
            sessions,
            media_store,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        ),
    )
    try:
        while True:
            handled = worker.run_once()
            if once:
                return
            if not handled:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        return
    finally:
        engine.dispose()


def _required_tool(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise typer.BadParameter(f"{name} is required")
    path = Path(value)
    if not path.is_file():
        raise typer.BadParameter(f"{name} does not point to a file")
    return path
