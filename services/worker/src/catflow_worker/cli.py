from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import typer
from dotenv import load_dotenv

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
from .fake_provider import FakeProviderGateway
from .media_jobs import MediaJobExecutor
from .project_posters import ProjectPosterGenerator
from .provider_media import ProviderMediaDownloader
from .runner import DurableJobWorker
from .runtime_support import AssetMediaResolver, JobResultDispatcher
from .segment_publisher import SegmentReferencePublisher

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run_worker(
    once: bool = typer.Option(False, help="Process at most one available lifecycle step."),
    poll_interval: float = typer.Option(1.0, min=0.1, max=30),
) -> None:
    """Run the durable planning, fake-media and FFmpeg worker."""
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
    local_results = MediaJobExecutor(
        sessions,
        media_store,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        poster_generator=poster_generator,
    )
    if provider_runtime.provider == "ark":
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
    else:
        provider = FakeProviderGateway()
        ark_results = None
        segment_publisher = None
    worker = DurableJobWorker(
        sessions,
        provider,
        worker_id=f"{socket.gethostname()}-{os.getpid()}",
        provider_name=provider_runtime.provider,
        studio_service=service,
        result_handler=JobResultDispatcher(
            sessions,
            local=local_results,
            ark=ark_results,
        ),
    )
    ready_file = paths.work_root / "worker-ready.json"
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_ready_file = ready_file.with_suffix(".partial")
    temporary_ready_file.write_text(
        json.dumps({"pid": os.getpid(), "provider": provider_runtime.provider}),
        encoding="utf-8",
    )
    temporary_ready_file.replace(ready_file)
    try:
        next_publication_cleanup = time.monotonic()
        while True:
            if segment_publisher is not None and time.monotonic() >= next_publication_cleanup:
                segment_publisher.delete_due()
                next_publication_cleanup = time.monotonic() + 60
            handled = worker.run_once()
            if once:
                return
            if not handled:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        return
    finally:
        ready_file.unlink(missing_ok=True)
        engine.dispose()


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
