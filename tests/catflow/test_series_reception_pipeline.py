from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from catflow.application.provider_config import ProviderRuntime
from catflow.application.series import (
    SeriesCreateCommand,
    SeriesEpisodeMaterializeCommand,
    SeriesEpisodeStoryGenerationCommand,
    SeriesPlanActivationCommand,
    SeriesPlanDraft,
    SeriesPlanGenerationCommand,
    SeriesPlanMaterializeCommand,
    SeriesSourceBeatDto,
)
from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    ShotPlanActivationCommand,
    ShotPlanGenerationCommand,
    StudioService,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app
from catflow_worker.ark_gateway import ArkTypedGateway
from catflow_worker.ark_job_gateway import ArkProviderJobGateway
from test_ark_gateway import Recorder, _settings
from test_workflow import _director_payload, _proposal

_FIXTURE = Path(__file__).with_name("fixtures") / "series-reception-rain.json"


def _service() -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=True),
            paid_calls_enabled=True,
        ),
    )


def test_saved_series_result_flows_to_an_exact_frozen_video_provider_request(
    tmp_path: Path,
) -> None:
    service = _service()
    repository = service._repository
    series = service.create_story_series(
        SeriesCreateCommand(
            title="雨天三章",
            adaptationPolicy="condense_mainline",
            premise="孩子和猫咪从白天到夜晚一起度过雨天",
            narrativeMode="anthology",
            plannedEpisodeCount=3,
            defaultEpisodeDurationSeconds=15,
            worldSetting="同一栋住宅、门口与窗边",
            emotionalDirection="从好奇玩耍到安静依偎",
        )
    )
    repository._series_source_beats[series.id] = [
        SeriesSourceBeatDto(
            id=uuid.uuid4(),
            seriesId=series.id,
            sourceUnitId=uuid.uuid4(),
            sourceUnitOrdinal=ordinal,
            bindingOrder=ordinal,
            title=f"雨天片段 {ordinal}",
            rawText=f"第 {ordinal} 个雨天生活片段",
            createdAt=datetime.now(UTC),
        )
        for ordinal in range(1, 4)
    ]
    series_preview = service.preview_series_plan(series.id)
    series_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=series_preview.input_hash,
            idempotencyKey="pipeline-series-provider-result",
        ),
    )
    raw_plan = SeriesPlanDraft.model_validate(
        json.loads(_FIXTURE.read_text(encoding="utf-8"))
    )
    pending = service.complete_series_plan_job(series_job.id, raw_plan)
    assert pending.disposition == "needs_input"
    normalized = service.materialize_series_plan(
        series.id,
        pending.id,
        SeriesPlanMaterializeCommand(
            basePlanVersionId=pending.id,
            source="saved_result",
            expectedSettingsHash=series_preview.settings_input_hash,
            idempotencyKey="pipeline-normalize-saved-result",
        ),
    )
    assert normalized.disposition == "candidate_ready"
    assert normalized.plan.episodes[1].source_coverage[0].coverage == "partial"
    service.activate_series_plan(
        series.id,
        normalized.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="pipeline-adopt-normalized-plan",
        ),
    )

    episode = service.list_series_episodes(series.id)[0]
    project = service.materialize_series_episode(
        series.id,
        episode.id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="pipeline-first-episode-project"),
    )
    story_preview = service.preview_series_episode_story(series.id, episode.id)
    story_job = service.create_series_episode_story_job(
        series.id,
        episode.id,
        SeriesEpisodeStoryGenerationCommand(
            expectedInputHash=story_preview.input_hash,
            idempotencyKey="pipeline-first-episode-story",
        ),
    )
    proposal = service.complete_series_episode_story_job(story_job.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)

    environment_preview = service.preview_asset_generation(
        project.id, AssetGenerationPreviewCommand(kind="environment")
    )
    environment_job = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=environment_preview.input_hash,
            idempotencyKey="pipeline-environment-request",
        ),
    )
    assert environment_job.frozen_input["compiledProviderPrompt"] == (
        environment_preview.compiled_provider_prompt
    )
    environment = service.register_asset(
        project.id, role="environment", sha256="e" * 64
    )
    service.select_asset(project.id, slot="environment", asset_id=environment.id)

    director_job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="pipeline-director-request"),
    )
    director = service.complete_shot_plan_job(director_job.id, _director_payload())
    adopted_director = service.activate_shot_plan(
        project.id,
        director.id,
        ShotPlanActivationCommand(
            expectedActiveShotPlanVersionId=None,
            idempotencyKey="pipeline-adopt-director-result",
        ),
    )

    video_preview = service.preview_video_generation(project.id)
    video_job = service.create_video_job(
        project.id,
        GenerationCommand(
            expectedInputHash=video_preview.input_hash,
            idempotencyKey="pipeline-video-request",
        ),
    )
    assert video_job.input_snapshot is not None
    assert video_preview.story_version_id == story.id
    assert video_preview.shot_plan_version_id == adopted_director.id
    assert video_job.frozen_input["compiledProviderPrompt"] == (
        video_preview.compiled_provider_prompt
    )

    reference_path = (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "canon"
        / "v4"
        / "episode-cat.png"
    )
    recorder = Recorder(SimpleNamespace(id="offline-video-task", request_id="offline-request"))
    gateway = ArkProviderJobGateway(
        ArkTypedGateway(
            replace(_settings(), video_model=video_job.model),
            client=SimpleNamespace(
                content_generation=SimpleNamespace(tasks=recorder),
            ),
        ),
        resolve_asset_paths=lambda asset_ids: (reference_path,) * len(asset_ids),
        extract_video_frames=lambda *_args: (),
        prepare_segment_media=lambda *_args: (reference_path,) * 3,
        publish_segment_reference=SimpleNamespace(
            publish_asset=lambda *_args: SimpleNamespace(
                url="https://example.com/offline-context.mp4",
                publication_id=uuid.uuid4(),
            )
        ),
    )
    submission = gateway.submit(
        job_id=video_job.id,
        kind=video_job.kind,
        frozen_input=video_job.frozen_input,
    )

    assert submission.task_id == "offline-video-task"
    assert recorder.calls[0]["content"][0]["text"] == (
        video_job.frozen_input["compiledProviderPrompt"]
    )

    video_bytes = b"offline-mocked-mp4-result"
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(video_bytes)
    video_asset = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        storage_key="output.mp4",
        sha256=sha256(video_bytes).hexdigest(),
        byte_size=len(video_bytes),
        producing_job_id=video_job.id,
    )
    repository._jobs[video_job.id] = video_job.model_copy(
        update={"status": "succeeded", "result_asset_ids": [video_asset.id]}
    )
    client = TestClient(
        create_app(
            service,
            settings=AppSettings(
                csrf_token="pipeline-csrf",
                allowed_hosts=("testserver",),
                allowed_origins=("http://127.0.0.1:8765",),
            ),
            media_store=LocalMediaStore(tmp_path),
        )
    )
    workspace_response = client.get(f"/api/v1/projects/{project.id}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["latestVideoJob"]["id"] == str(video_job.id)
    assert workspace["latestVideoJob"]["status"] == "succeeded"
    assert workspace["activeStory"]["id"] == str(story.id)
    content = client.get(f"/api/v1/assets/{video_asset.id}/content")
    assert content.status_code == 200
    assert content.content == video_bytes
    assert content.headers["content-type"] == "video/mp4"
