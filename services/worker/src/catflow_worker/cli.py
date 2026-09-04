from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import typer
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import StudioService
from catflow.config import RuntimePaths
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.object_storage import ObjectPublisherRuntime
from catflow.infrastructure.postgres_repository import PostgresStudioRepository

from .ark_gateway import ArkGatewaySettings, ArkTypedGateway
from .ark_job_gateway import ArkProviderJobGateway
from .ark_results import ArkResultLandingService
from .lifecycle import WorkerHeartbeat, WorkerSupervisor
from .media_jobs import LocalMediaJobExecutor
from .project_posters import ProjectPosterGenerator
from .provider_media import ProviderMediaDownloader
from .runner import DurableJobWorker
from .runtime_support import AssetMediaResolver, JobResultDispatcher
from .segment_publisher import SegmentReferencePublisher

app = typer.Typer(no_args_is_help=True)
LOGGER = logging.getLogger(__name__)


@app.command("run")
def run_worker(
    once: bool = typer.Option(False, help="Process at most one available lifecycle step."),
    poll_interval: float = typer.Option(1.0, min=0.1, max=30),
) -> None:
    """Run the durable Ark and FFmpeg worker."""
    _run_worker(once=once, poll_interval=poll_interval)


def _run_worker(*, once: bool, poll_interval: float) -> None:
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    object_publisher_runtime = ObjectPublisherRuntime.from_env()
    provider_runtime = ProviderRuntime.from_env(
        segment_reference_publishing_ready=object_publisher_runtime.status.ready
    )
    ffmpeg_path = _required_tool("FFMPEG_PATH")
    ffprobe_path = _required_tool("FFPROBE_PATH")
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=provider_runtime)
    media_store = LocalMediaStore(paths.media_root)
    poster_generator = ProjectPosterGenerator(
        sessions,
        media_store,
        ffmpeg_path=ffmpeg_path,
    )
    local_results = LocalMediaJobExecutor(
        sessions,
        media_store,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        poster_generator=poster_generator,
    )
    typed_gateway = ArkTypedGateway(ArkGatewaySettings.from_env())
    resolver = AssetMediaResolver(
        sessions,
        media_store,
        ffmpeg_path=ffmpeg_path,
    )
    segment_publisher = (
        SegmentReferencePublisher(sessions, object_publisher_runtime.store)
        if object_publisher_runtime.status.ready and object_publisher_runtime.store is not None
        else None
    )
    provider = ArkProviderJobGateway(
        typed_gateway,
        resolve_asset_paths=resolver.resolve_paths,
        extract_video_frames=resolver.extract_video_frames,
        prepare_segment_media=resolver.prepare_segment_media,
        publish_segment_reference=segment_publisher,
    )
    ark_results = ArkResultLandingService(
        sessions,
        media_store,
        studio_service=service,
        downloader=ProviderMediaDownloader(),
        ffprobe_path=ffprobe_path,
        poster_generator=poster_generator,
    )
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    worker = DurableJobWorker(
        sessions,
        provider,
        worker_id=worker_id,
        result_handler=JobResultDispatcher(
            sessions,
            local=local_results,
            ark=ark_results,
        ),
    )
    ready_file = paths.work_root / "worker-ready.json"
    try:
        with WorkerHeartbeat(ready_file, worker_id=worker_id):
            next_publication_cleanup = time.monotonic()
            database_failures = 0
            while True:
                if segment_publisher is not None and time.monotonic() >= next_publication_cleanup:
                    segment_publisher.delete_due()
                    next_publication_cleanup = time.monotonic() + 60
                try:
                    handled = worker.run_once()
                    database_failures = 0
                except SQLAlchemyError:
                    database_failures += 1
                    retry_in = min(30.0, float(2 ** min(database_failures - 1, 5)))
                    LOGGER.exception(
                        "worker_database_connection_lost retry_in_seconds=%s",
                        retry_in,
                    )
                    engine.dispose()
                    if once:
                        raise
                    time.sleep(retry_in)
                    continue
                if once:
                    return
                if not handled:
                    time.sleep(poll_interval)
    except KeyboardInterrupt:
        return
    finally:
        engine.dispose()


@app.command("supervise")
def supervise_worker(
    poll_interval: float = typer.Option(1.0, min=0.1, max=30),
) -> None:
    """Keep the local durable worker running without changing queued jobs."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)

    def start_child() -> subprocess.Popen[bytes]:
        child_environment = None
        executable = sys.executable
        if os.name == "nt":
            child_environment = os.environ.copy()
            child_environment["__PYVENV_LAUNCHER__"] = sys.executable
            executable = getattr(sys, "_base_executable", sys.executable)
        return subprocess.Popen(
            [
                executable,
                "-m",
                "catflow_worker.cli",
                "run",
                "--poll-interval",
                str(poll_interval),
            ],
            cwd=project_root,
            env=child_environment,
        )

    stop = threading.Event()
    supervisor = WorkerSupervisor(
        paths.work_root / "worker-supervisor.json",
        process_factory=start_child,
    )
    try:
        supervisor.run(stop)
    except KeyboardInterrupt:
        stop.set()


def _required_tool(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise typer.BadParameter(f"{name} is required")
    path = Path(value)
    if not path.is_file():
        raise typer.BadParameter(f"{name} does not point to a file")
    return path


@app.command("backfill-posters")
def backfill_posters(
    limit: int = typer.Option(200, min=1, max=10_000),
) -> None:
    """Create missing local project posters without changing source videos."""
    project_root = Path(os.environ.get("CATFLOW_ROOT", Path.cwd())).resolve()
    load_dotenv(project_root / ".env", override=False)
    paths = RuntimePaths.from_env(project_root)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    generator = ProjectPosterGenerator(
        sessions,
        LocalMediaStore(paths.media_root),
        ffmpeg_path=_required_tool("FFMPEG_PATH"),
    )
    try:
        processed, failed = generator.backfill_missing(limit=limit)
        typer.echo(json.dumps({"processed": processed, "failed": failed}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    app()
