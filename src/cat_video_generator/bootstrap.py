"""Composition root for the V5 video-clip workflow monolith."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

from .application.aigc_canvas import AigcCanvasService
from .application.canon import CanonRepairService
from .application.production_recipes import ProductionRecipeService
from .application.sequence_service import SequenceService
from .application.shot_queue import ProjectEditingService, ShotProductionService
from .application.universal_media_worker import UniversalMediaWorker, VideoFilmstripExecutor
from .application.universal_video_edit import UniversalVideoEditExecutor
from .config import DatabaseOperation, DatabaseSettings, RuntimeSettings, load_local_env
from .infrastructure.ark.runtime import RuntimeArkGateway, RuntimeConfigurationManager
from .infrastructure.db.aigc_canvas_repository import SqlAlchemyAigcCanvasRepository
from .infrastructure.db.durable_queue import DurableWorkflowQueue
from .infrastructure.db.production_recipe_repository import (
    SqlAlchemyProductionRecipeRepository,
)
from .infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from .infrastructure.db.session import (
    ALEMBIC_HEAD,
    create_database_engine,
    create_session_factory,
    ensure_database_ready,
)
from .infrastructure.media.qc import FfmpegFrameExtractor, FfprobeMediaProbe
from .infrastructure.media.storage import LocalAssetStore


@dataclass(slots=True)
class RuntimeContainer:
    engine: Engine
    repository: SqlAlchemyWorkflowRepository
    editing: ProjectEditingService
    production: ShotProductionService
    sequences: SequenceService
    canon: CanonRepairService
    canvas_v2: AigcCanvasService
    production_recipes: ProductionRecipeService
    workflow_queue: DurableWorkflowQueue
    media_canvas_worker: UniversalMediaWorker
    runtime_settings: RuntimeSettings
    runtime_configuration: RuntimeConfigurationManager
    alembic_revision: str

    def close(self) -> None:
        self.engine.dispose()


@dataclass(slots=True)
class DiagnosticContainer:
    engine: Engine
    runtime_settings: RuntimeSettings
    database_name: str
    alembic_revision: str | None

    def close(self) -> None:
        self.engine.dispose()


def build_runtime_container() -> RuntimeContainer:
    """Build the studio without issuing any provider request.

    Missing Ark credentials do not prevent local project editing.  Paid
    endpoints fail at their natural boundary until the environment is fixed.
    """

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = _ready_engine(database)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyWorkflowRepository(
        sessions,
        asset_root=runtime.asset_root,
    )
    canvas_repository = SqlAlchemyAigcCanvasRepository(
        sessions,
        asset_root=runtime.asset_root,
    )
    runtime_configuration = RuntimeConfigurationManager(
        runtime,
        Path("var/config/runtime-settings.json"),
    )
    gateway = RuntimeArkGateway(runtime_configuration)
    store = LocalAssetStore(
        work_root=runtime.work_root,
        asset_root=runtime.asset_root,
        ffmpeg_path=runtime.ffmpeg_path,
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    extractor = (
        None
        if runtime.ffmpeg_path is None
        else FfmpegFrameExtractor(ffmpeg_path=runtime.ffmpeg_path, work_root=runtime.work_root)
    )
    workflow_queue = DurableWorkflowQueue(sessions, gateway=gateway)
    editing = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name=runtime_configuration.provider_profile,
    )
    production = ShotProductionService(
        repository=repository,
        gateway=gateway,
        asset_store=store,
        media_probe=probe,
        frame_extractor=extractor,
        provider_name=runtime_configuration.provider_profile,
        resolution=runtime_configuration.video_resolution,
        runtime_preflight=runtime_configuration,
        enable_video_advice=runtime_configuration.semantic_review_enabled,
        poll_interval_seconds=runtime.ark_poll_interval_seconds,
        task_timeout_seconds=runtime.ark_task_timeout_seconds,
    )
    sequences = SequenceService(
        repository=repository,
        asset_store=store,
        media_probe=probe,
        resolution=runtime_configuration.video_resolution,
        runtime_preflight=runtime_configuration,
    )
    canvas_v2 = AigcCanvasService(
        repository=canvas_repository,
        director=gateway,
        provider_name=runtime_configuration.provider_profile,
    )
    production_recipes = ProductionRecipeService(
        repository=SqlAlchemyProductionRecipeRepository(sessions),
        story_workflow=canvas_v2,
        shot_workflow=production,
        sequence_workflow=sequences,
        asset_root=runtime.asset_root,
    )
    return RuntimeContainer(
        engine=engine,
        repository=repository,
        editing=editing,
        production=production,
        sequences=sequences,
        canon=CanonRepairService(repository=repository, asset_store=store),
        canvas_v2=canvas_v2,
        production_recipes=production_recipes,
        workflow_queue=workflow_queue,
        media_canvas_worker=UniversalMediaWorker(
            queue=workflow_queue,
            repository=canvas_repository,
            gateway=gateway,
            asset_store=store,
            worker_id="media-canvas-worker",
            recipe_task_executor=production_recipes,
            shot_video_executor=production,
            provider_poll_interval_seconds=runtime.ark_poll_interval_seconds,
            filmstrip_executor=VideoFilmstripExecutor(
                repository=canvas_repository,
                frame_extractor=extractor,
                asset_store=store,
            ),
            video_edit_executor=UniversalVideoEditExecutor(
                repository=canvas_repository,
                gateway=gateway,
                asset_store=store,
                media_probe=probe,
                frame_extractor=extractor,
                resolution=runtime_configuration.video_resolution,
            ),
        ),
        runtime_settings=runtime,
        runtime_configuration=runtime_configuration,
        alembic_revision=ALEMBIC_HEAD,
    )


def build_diagnostic_container() -> DiagnosticContainer:
    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(
        database,
        DatabaseOperation.READ_ONLY_SMOKE,
        pool_size=1,
        max_overflow=0,
    )
    try:
        with engine.connect() as connection:
            database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
            revision = connection.execute(
                text(f"SELECT version_num FROM {database.schema}.alembic_version")
            ).scalar_one_or_none()
    except Exception:
        engine.dispose()
        raise
    return DiagnosticContainer(
        engine=engine,
        runtime_settings=runtime,
        database_name=database_name,
        alembic_revision=revision,
    )


def _ready_engine(database: DatabaseSettings) -> Engine:
    engine = create_database_engine(database, DatabaseOperation.RUNTIME)
    try:
        ensure_database_ready(engine, database)
    except Exception:
        engine.dispose()
        raise
    return engine
