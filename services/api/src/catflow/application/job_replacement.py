"""Rebuild current inputs through the existing generation entry points."""

from __future__ import annotations

import uuid

from .job_execution import ReplacementGenerationCommand, UnknownJobReplacement
from .series import (
    SeriesEpisodeStoryGenerationCommand,
    SeriesPlanGenerationCommand,
    SeriesPlanSegmentCommand,
    SeriesPlanSegmentGenerationCommand,
)
from .service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    GenerationPreviewCommand,
    ImageDiagnosisCommand,
    PlannerMessageCommand,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    ShotPlanGenerationCommand,
    StudioConflictError,
    StudioService,
    VideoDiagnosisCommand,
)
from .shot_production import ShotMediaCommand, ShotMediaPreviewCommand
from .story_imports import StoryImportPreviewCommand, StoryImportReanalyzeCommand


def replace_unknown_job(
    service: StudioService, job_id: uuid.UUID, command: ReplacementGenerationCommand | None = None
):
    old = service.get_job(job_id)
    if command:
        for successor_id in old.successor_job_ids:
            successor = service.get_job(successor_id)
            if successor.idempotency_key == command.idempotency_key:
                if successor.frozen_input.get("executionInputHash") != command.input_hash:
                    raise StudioConflictError("此提交编号已经绑定另一份输入。")
                return successor
    if (
        old.status != "submission_unknown"
        or "prepare_replacement" not in old.execution.available_actions
    ):
        raise StudioConflictError("此任务不满足结果未知的重新准备条件。")
    frozen = old.frozen_input
    common = {
        "idempotencyKey": command.idempotency_key if command else f"prepare:{uuid.uuid4()}",
        "prepareOnly": command is None,
        "replacementJobId": old.id,
        "replacement": UnknownJobReplacement(
            jobId=old.id, inputHash=command.input_hash, acknowledgeDuplicateCharge=True
        )
        if command
        else None,
    }
    project = old.project_id
    # A shot must use its current shot design, not the whole-video default.
    if frozen.get("purpose") in {"shot_frame", "shot_video"}:
        plans = service.list_shot_plans(project)
        plan = next((plan for plan in plans if plan.active), None)
        if plan is None:
            raise StudioConflictError("当前分镜不存在，请先更新镜头输入。")
        target = ShotMediaPreviewCommand(
            shotPlanVersionId=plan.id, shotId=frozen["targetShotId"], purpose=frozen["purpose"]
        )
        preview = service.preview_shot_media(project, target)
        return service.create_shot_media_job(
            project,
            ShotMediaCommand(
                **target.model_dump(), expectedInputHash=preview["inputHash"], **common
            ),
        )
    if old.kind == "plan_shots":
        return service.create_shot_plan_generation_job(project, ShotPlanGenerationCommand(**common))
    if old.kind == "plan_story":
        snapshot = service.get_planner(project)
        return service.enqueue_planner_message(
            project,
            PlannerMessageCommand(
                text=frozen["text"], expectedContextRevision=snapshot.context_revision, **common
            ),
        )
    if old.kind == "plan_series_episode":
        series_id, episode_id = uuid.UUID(frozen["seriesId"]), uuid.UUID(frozen["seriesEpisodeId"])
        preview = service.preview_series_episode_story(
            series_id, episode_id, additional_notes=frozen.get("additionalNotes")
        )
        return service.create_series_episode_story_job(
            series_id,
            episode_id,
            SeriesEpisodeStoryGenerationCommand(
                expectedInputHash=preview.input_hash,
                additionalNotes=frozen.get("additionalNotes"),
                **common,
            ),
        )
    if old.kind == "plan_series":
        preview = service.preview_series_plan(old.series_id)
        return service.create_series_plan_job(
            old.series_id,
            SeriesPlanGenerationCommand(expectedInputHash=preview.input_hash, **common),
        )
    if old.kind == "plan_series_segment":
        target = SeriesPlanSegmentCommand(
            startEpisodeOrder=frozen["startEpisodeOrder"],
            requestedEpisodeCount=frozen["plannedEpisodeCount"],
            expectedSeriesPlanVersionId=frozen["expectedSeriesPlanVersionId"],
            expectedPreviousSegmentVersionId=frozen.get("expectedPreviousSegmentVersionId"),
        )
        preview = service.preview_series_plan_segment(old.series_id, target)
        return service.create_series_plan_segment_job(
            old.series_id,
            SeriesPlanSegmentGenerationCommand(
                **target.model_dump(), expectedInputHash=preview.input_hash, **common
            ),
        )
    if old.kind == "analyze_story_source":
        source = service.get_story_import(old.story_source_document_id)
        preview = service.preview_story_import(
            StoryImportPreviewCommand(
                rawText=source.raw_text,
                sourceFormat=source.source_format,
                fileName=source.file_name,
            )
        )
        return service.reanalyze_story_import(
            source.id, StoryImportReanalyzeCommand(expectedInputHash=preview.input_hash, **common)
        )
    if old.kind == "generate_image":
        preview = service.preview_asset_generation(
            project, AssetGenerationPreviewCommand(kind=frozen["role"])
        )
        return service.create_asset_generation_job(
            project,
            AssetGenerationCommand(
                kind=frozen["role"], expectedInputHash=preview.input_hash,
                environmentDraftRevision=preview.environment_draft.revision if preview.environment_draft else None, **common
            ),
        )
    if old.kind == "generate_video":
        target = GenerationPreviewCommand(
            includePreviousEpisodeVideo=bool(frozen.get("previousEpisodeVideoAssetId"))
        )
        preview = service.preview_video_generation(
            project, include_previous_episode_video=target.include_previous_episode_video
        )
        return service.create_video_job(
            project,
            GenerationCommand(
                **target.model_dump(), expectedInputHash=preview.input_hash, **common
            ),
        )
    if old.kind == "diagnose_image":
        return service.create_image_diagnosis_job(
            project, ImageDiagnosisCommand(assetId=frozen["candidateAssetId"], **common)
        )
    if old.kind == "diagnose_video":
        return service.create_video_diagnosis_job(
            project, VideoDiagnosisCommand(assetId=frozen["videoAssetId"], **common)
        )
    if old.kind == "regenerate_video_segment":
        repair = service.get_video_repair(old.video_repair_id)
        aliases = {
            field.alias or name for name, field in SegmentRepairPreviewCommand.model_fields.items()
        }
        target = {
            key: value
            for key, value in repair.preview.model_dump(mode="json", by_alias=True).items()
            if key in aliases
        }
        preview = service.preview_video_repair(
            project, SegmentRepairPreviewCommand.model_validate(target)
        )
        return service.create_video_repair_job(
            project,
            SegmentRepairCreateCommand(**target, expectedInputHash=preview.input_hash, **common),
        )
    raise StudioConflictError("此类任务请从原制作入口重新准备。")
