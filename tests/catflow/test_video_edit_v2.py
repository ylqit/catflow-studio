import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from catflow.application import service as dto
from catflow.application.provider_config import ProviderRuntime
from catflow.application.video_edit import (
    VideoEditOptions,
    calculate_edit_window,
    compile_edit_prompt,
)
from catflow.domain.video_repairs import FrameRange
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.infrastructure.models import JobRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


@pytest.mark.parametrize(
    "start,end,total", [(0, 1, 361), (278, 361, 361), (0, 96, 361), (0, 360, 361)]
)
def test_reference_window_is_minimal_and_issue_is_unchanged(start, end, total):
    issue = FrameRange(startFrame=start, endFrame=end)
    options = VideoEditOptions(editContractVersion=2)
    window = calculate_edit_window(options, issue, total_frames=total, from_frame=False)
    assert window.issue_range == issue
    assert window.generation_range.duration_frames == max(48, end - start)
    assert window.generation_range.end_frame <= total
    assert window.provider_duration_seconds >= 4
    assert window.candidate_core_range.duration_frames == end - start


def test_selection_requires_minimum_reference_without_changing_selection():
    with pytest.raises(ValueError, match="contextMode"):
        calculate_edit_window(
            VideoEditOptions(editContractVersion=2, contextMode="selection"),
            FrameRange(startFrame=0, endFrame=1),
            total_frames=100,
            from_frame=False,
        )


def test_prompt_retains_user_static_instruction_and_actual_roles():
    options = VideoEditOptions(
        editContractVersion=2, preserveContent="  保持静止\n不眨眼  ", avoidProblems="不要移动"
    )
    window = calculate_edit_window(
        options, FrameRange(startFrame=278, endFrame=361), total_frames=361, from_frame=False
    )
    prompt = compile_edit_prompt(
        options, instruction="站着不动。", window=window, image_roles=(), from_frame=False
    )
    assert "站着不动。" in prompt
    assert options.preserve_content in prompt
    assert "动作必须" not in prompt
    assert "静止停帧" not in prompt
    assert "anchor_out" not in prompt


def edit_fixture(repository=None):
    repo = repository or MemoryStudioRepository()
    service = dto.StudioService(
        repo,
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="test",
            image_model="test",
            video_model="test",
            diagnostic_model="test",
            capability_revision="test",
            paid_calls_enabled=True,
            maximum_video_references=9,
            segment_reference_publishing_ready=True,
        ),
    )
    project = service.create_project(
        dto.ProjectCreate(title="generic edit", theme="test", targetDurationSeconds=12)
    )
    video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256=uuid.uuid4().hex * 2,
        storage_key="test/video.mp4",
        byte_size=100,
        metadata={"durationFrames": 361, "frameRateNumerator": 24, "frameRateDenominator": 1},
    )
    draft = service.create_video_edit_draft(
        project.id,
        dto.VideoEditDraftCreateCommand(
            sourceVideoAssetId=video.id, idempotencyKey=str(uuid.uuid4())
        ),
    )
    command = dto.SegmentRepairPreviewCommand(
        editContractVersion=2,
        editDraftId=draft.id,
        baseEditVersionId=draft.head_edit_version_id,
        baseVideoAssetId=video.id,
        issueRange={"startFrame": 278, "endFrame": 361},
        instruction="  保持静止\n不要改变姿态。  ",
    )
    return service, repo, project, video, draft, command


def test_v2_preview_no_original_out_or_current_project_reference_fallback():
    service, repo, project, video, draft, command = edit_fixture()
    preview = service.preview_video_repair(project.id, command)
    assert command.end_state_policy == "follow_instruction"
    assert [ref.role for ref in preview.image_references] == ["anchor_in"]
    assert preview.instruction == "  保持静止\n不要改变姿态。  "
    assert preview.instruction in preview.compiled_provider_prompt
    assert preview.input_snapshot.prompt_compiler_revision == "segment-edit-v7"
    changed = service.preview_video_repair(
        project.id, command.model_copy(update={"instruction": "离开画面"})
    )
    assert changed.media_input_hash == preview.media_input_hash
    assert changed.input_hash != preview.input_hash
    no_images = service.preview_video_repair(
        project.id, command.model_copy(update={"include_in_anchor": False, "reference_roles": []})
    )
    assert no_images.image_references == []
    assert no_images.media_input_hash != preview.media_input_hash
    explicit_out = service.preview_video_repair(
        project.id, command.model_copy(update={"end_state_policy": "match_original"})
    )
    assert [ref.role for ref in explicit_out.image_references] == ["anchor_in", "anchor_out"]


def test_v2_local_preparation_reuses_material_when_text_changes():
    service, repo, project, video, draft, command = edit_fixture()
    first = service.prepare_segment_references(
        project.id, dto.SegmentReferencePreparationCommand(**command.model_dump())
    )
    second = service.prepare_segment_references(
        project.id,
        dto.SegmentReferencePreparationCommand(
            **command.model_copy(update={"instruction": "改变灯光"}).model_dump()
        ),
    )
    assert first.id == second.id
    assert first.frozen_input["referenceRevision"] == 3
    assert first.frozen_input["mediaInputHash"]


def test_v2_partial_from_frame_has_no_strict_last_frame():
    service, repo, project, video, draft, command = edit_fixture()
    command = command.model_copy(
        update={
            "generation_mode": "from_frame",
            "anchor_start_frame": 278,
            "anchor_end_frame": 360,
            "audio_mode": "preserve_current",
        }
    )
    preview = service.preview_video_repair(project.id, command)
    assert [ref.role for ref in preview.image_references] == ["first_frame"]
    assert preview.video_reference is None
    assert preview.warnings[0]["code"] == "last_frame_not_strict"


def test_legacy_missing_version_keeps_original_out_and_minimum_selection():
    with pytest.raises(ValueError, match="96"):
        dto.SegmentRepairPreviewCommand(
            baseVideoAssetId=uuid.uuid4(),
            issueRange={"startFrame": 0, "endFrame": 1},
            instruction="still",
        )
    command = dto.SegmentRepairPreviewCommand(
        baseVideoAssetId=uuid.uuid4(),
        issueRange={"startFrame": 0, "endFrame": 96},
        instruction="still",
    )
    assert command.end_state_policy == "match_original"


def test_input_cas_accepts_incomplete_form_without_mutating_video_revision():
    service, repo, project, video, draft, command = edit_fixture()

    def save(text):
        try:
            return service.update_video_edit_draft_input(
                project.id,
                draft.id,
                dto.VideoEditDraftInputCommand(
                    expectedRevision=0, editingInput={"editContractVersion": 2, "instruction": text}
                ),
            )
        except dto.StudioConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["", "new input"]))
    assert sum(item is not None for item in results) == 1
    stored = service.get_video_edit_draft(project.id, draft.id)
    assert stored.input_revision == 1
    assert stored.head_edit_version_id == draft.head_edit_version_id
    with pytest.raises(dto.StudioValidationError):
        service.update_video_edit_draft_input(
            project.id,
            draft.id,
            dto.VideoEditDraftInputCommand(
                expectedRevision=1, editingInput={"editContractVersion": 3}
            ),
        )


def test_explicit_planner_freezes_selection_source_and_text_without_reference_job():
    service, repo, project, video, draft, command = edit_fixture()
    service.preview_video_repair(project.id, command)
    assert repo.list_project_jobs(project.id) == []
    plan = service.create_video_edit_plan_job(
        project.id,
        dto.VideoEditPlanCommand(**command.model_dump(), idempotencyKey="explicit-plan-1"),
    )
    assert plan.kind == "plan_video_edit"
    assert plan.id in {job.id for job in service.list_video_draft_jobs(project.id, draft.id)}
    assert plan.frozen_input["command"]["instruction"] == command.instruction
    samples = plan.frozen_input["frameSamples"]
    assert samples[0]["frameNumber"] == 278
    assert samples[-1]["frameNumber"] == 360
    assert plan.frozen_input["inputEdl"]["videoSegments"][0]["assetId"] == str(video.id)
    assert plan.frozen_input["executionContract"]["protocol"] == "responses"
    assert [job.kind for job in repo.list_project_jobs(project.id)] == ["plan_video_edit"]


def test_postgres_draft_input_cas_and_explicit_plan_survive_reload():
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service, repo, project, video, draft, command = edit_fixture(PostgresStudioRepository(sessions))

    def save(value):
        try:
            return repo.update_video_edit_draft_input(
                draft.id, 0, {"editContractVersion": 2, "instruction": value}
            )
        except dto.StudioConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(pool.map(save, ["A", "B"]))
    assert sum(item is not None for item in updates) == 1
    reloaded = PostgresStudioRepository(sessions).get_video_edit_draft(draft.id)
    assert reloaded.input_revision == 1
    assert reloaded.editing_input["instruction"] in {"A", "B"}
    assert reloaded.head_edit_version_id == draft.head_edit_version_id
    plan = service.create_video_edit_plan_job(
        project.id,
        dto.VideoEditPlanCommand(**command.model_dump(), idempotencyKey=str(uuid.uuid4())),
    )
    assert repo.get_job(plan.id).kind == "plan_video_edit"
    with sessions.begin() as session:
        row = session.get(JobRecord, plan.id)
        row.status = "cancelled"
    engine.dispose()


def ready_preparation(service, repo, project, command):
    preparation = service.prepare_segment_references(
        project.id, dto.SegmentReferencePreparationCommand(**command.model_dump())
    )
    assets = []
    for index, ref in enumerate(preparation.frozen_input["imageReferences"]):
        if not ref["derived"]:
            continue
        asset = service.register_asset(
            project.id,
            role="repair_anchor_in"
            if ref["role"] in {"anchor_in", "first_frame"}
            else "repair_anchor_out",
            media_type="image",
            sha256=str(index + 1) * 64,
            storage_key="test/anchor.png",
            byte_size=100,
            producing_job_id=preparation.id,
        )
        assets.append(asset.id)
    if preparation.frozen_input["videoReference"]:
        reference_range = preparation.frozen_input["generationRange"]
        asset = service.register_asset(
            project.id,
            role="repair_context",
            media_type="video",
            sha256="f" * 64,
            storage_key="test/context.mp4",
            byte_size=100,
            producing_job_id=preparation.id,
            metadata={
                "durationFrames": reference_range["endFrame"] - reference_range["startFrame"],
                "paddedTailFrames": 0,
            },
        )
        assets.append(asset.id)
    repo._jobs[preparation.id] = preparation.model_copy(
        update={"status": "succeeded", "result_asset_ids": assets}
    )
    return preparation


@pytest.mark.parametrize("with_in", [False, True])
def test_v2_sdk_zero_or_one_anchor_exact_prompt_and_text_reference_reuse(tmp_path, with_in):
    from dataclasses import replace
    from types import SimpleNamespace

    from catflow_worker.ark_gateway import ArkTypedGateway
    from catflow_worker.ark_job_gateway import ArkProviderJobGateway
    from test_ark_gateway import Recorder, _image, _settings

    service, repo, project, video, draft, command = edit_fixture()
    command = command.model_copy(
        update={
            "include_in_anchor": with_in,
            "reference_roles": [],
            "avoid_problems": "  no text  \n",
        }
    )
    prep = ready_preparation(service, repo, project, command)
    command = command.model_copy(
        update={"reference_preparation_job_id": prep.id, "instruction": "  光线保持不变。  "}
    )
    preview = service.preview_video_repair(project.id, command)
    job = service.create_video_repair_job(
        project.id,
        dto.SegmentRepairCreateCommand(
            **command.model_dump(),
            expectedInputHash=preview.input_hash,
            idempotencyKey="v2-generate-" + str(with_in),
        ),
    )
    image = _image(tmp_path / "anchor.png", "blue")
    recorder = Recorder(SimpleNamespace(id="sdk-task"))
    typed = ArkTypedGateway(
        replace(_settings(), video_model=job.model),
        client=SimpleNamespace(content_generation=SimpleNamespace(tasks=recorder)),
    )
    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda ids: (image,) * len(ids),
        extract_video_frames=lambda *_: (),
        prepare_segment_media=lambda *_: (image,) * 3,
        publish_segment_reference=SimpleNamespace(
            publish_asset=lambda *_: SimpleNamespace(
                url="https://example.com/context.mp4", publication_id=uuid.uuid4()
            )
        ),
    )
    gateway.submit(job_id=job.id, kind=job.kind, frozen_input=job.frozen_input)
    assert preview.compiled_provider_prompt.endswith("  no text  \n")
    assert preview.input_snapshot.prompt.endswith("  no text  \n")
    assert preview.input_snapshot.negative_prompt == "  no text  \n"
    content = recorder.calls[0]["content"]
    assert (
        content[0]["text"]
        == preview.compiled_provider_prompt
        == job.input_snapshot.compiled_provider_prompt
    )
    assert job.frozen_input["instruction"] == command.instruction
    assert [item["type"] for item in content] == ["text", "video_url"] + (
        ["image_url"] if with_in else []
    )


def test_optional_planning_gateway_passes_ordered_actual_timeline_frames(tmp_path):
    from types import SimpleNamespace

    from catflow.application.gateways import StructuredProviderResult
    from catflow_worker.ark_job_gateway import ArkProviderJobGateway

    service, repo, project, video, draft, command = edit_fixture()
    job = service.create_video_edit_plan_job(
        project.id,
        dto.VideoEditPlanCommand(**command.model_dump(), idempotencyKey="planner-gateway"),
    )
    frames = tuple(
        tmp_path / f"{item['frameNumber']}.png" for item in job.frozen_input["frameSamples"]
    )
    calls = []

    def plan(**kwargs):
        calls.append(kwargs)
        return StructuredProviderResult(
            payload={}, response_id="response", model="test", usage={}, request_hash="a" * 64
        )

    gateway = ArkProviderJobGateway(
        SimpleNamespace(plan_shots=plan),
        resolve_asset_paths=lambda _: (),
        extract_video_frames=lambda *_: (),
        prepare_edit_plan_frames=lambda _id, frozen: frames,
    )
    result = gateway.submit(job_id=job.id, kind=job.kind, frozen_input=job.frozen_input)
    assert calls[0]["image_paths"] == frames
    assert calls[0]["prompt"] == job.frozen_input["prompt"]
    assert "不确定" in calls[0]["prompt"]
    assert result.result["responseId"] == "response"


def test_real_v2_reference_and_continuation_render_actual_timeline_with_audio(tmp_path):
    import hashlib
    import os
    import subprocess
    from pathlib import Path

    from PIL import Image
    from sqlalchemy import update

    from catflow.infrastructure.media import LocalMediaStore
    from catflow.infrastructure.models import AssetRecord
    from catflow_worker.media_jobs import LocalMediaJobExecutor
    from catflow_worker.media_probe import inspect_video
    from catflow_worker.runtime_support import AssetMediaResolver

    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service, repo, project, unused, draft_unused, command_unused = edit_fixture(
        PostgresStudioRepository(sessions)
    )
    store = LocalMediaStore(tmp_path / "media")
    ffmpeg, ffprobe = Path(os.environ["FFMPEG_PATH"]), Path(os.environ["FFPROBE_PATH"])
    executor = LocalMediaJobExecutor(sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    resolver = AssetMediaResolver(
        sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe, timeline_renderer=executor
    )
    source = store.resolve("test/source.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=96x160:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(361 / 24),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    facts = inspect_video(source, ffprobe)
    assert facts["durationFrames"] == 361 and facts["hasAudio"]
    video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        storage_key="test/source.mp4",
        byte_size=source.stat().st_size,
        metadata={**facts, "frameRateNumerator": 24, "frameRateDenominator": 1},
    )
    draft = service.create_video_edit_draft(
        project.id,
        dto.VideoEditDraftCreateCommand(
            sourceVideoAssetId=video.id, idempotencyKey=str(uuid.uuid4())
        ),
    )
    command = dto.SegmentRepairPreviewCommand(
        editContractVersion=2,
        baseVideoAssetId=video.id,
        editDraftId=draft.id,
        baseEditVersionId=draft.head_edit_version_id,
        issueRange={"startFrame": 278, "endFrame": 361},
        instruction="变成蓝色，保持不动。",
        includeInAnchor=False,
        referenceRoles=[],
        audioMode="preserve_current",
    )
    prep = service.prepare_segment_references(
        project.id, dto.SegmentReferencePreparationCommand(**command.model_dump())
    )
    resolver.store_reference_preparation(prep.id)
    with sessions.begin() as session:
        session.execute(update(JobRecord).where(JobRecord.id == prep.id).values(status="succeeded"))
        refs = list(
            session.scalars(select(AssetRecord).where(AssetRecord.producing_job_id == prep.id))
        )
        assert [ref.role for ref in refs] == ["repair_context"]
        assert refs[0].metadata_json["durationFrames"] == 83
        assert refs[0].metadata_json["paddedTailFrames"] == 0
    command = command.model_copy(update={"reference_preparation_job_id": prep.id})
    preview = service.preview_video_repair(project.id, command)
    job = service.create_video_repair_job(
        project.id,
        dto.SegmentRepairCreateCommand(
            **command.model_dump(),
            expectedInputHash=preview.input_hash,
            idempotencyKey=str(uuid.uuid4()),
        ),
    )
    candidate_path = store.resolve("test/candidate.mp4")
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=96x160:r=24",
            "-frames:v",
            "96",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(candidate_path),
        ],
        check=True,
        capture_output=True,
    )
    candidate = service.register_asset(
        project.id,
        role="repair_candidate",
        media_type="video",
        sha256=hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        storage_key="test/candidate.mp4",
        byte_size=candidate_path.stat().st_size,
        producing_job_id=job.id,
        metadata={
            **inspect_video(candidate_path, ffprobe),
            "frameRateNumerator": 24,
            "frameRateDenominator": 1,
        },
    )
    repo.set_video_repair_status(
        job.video_repair_id, status="candidate_ready", candidate_asset_id=candidate.id
    )
    with sessions.begin() as session:
        session.execute(update(JobRecord).where(JobRecord.id == job.id).values(status="succeeded"))
    base = repo.get_edit(draft.head_edit_version_id)
    result = service.create_draft_preview(
        project.id,
        draft.id,
        dto.VideoDraftPreviewCommand(
            expectedEditVersionId=base.id,
            expectedTimelineHash=base.timeline_hash,
            repairId=job.video_repair_id,
            placement={
                "candidateSourceRange": {"startFrame": 5, "endFrame": 88},
                "audioPolicy": "preserve_current",
            },
            idempotencyKey=str(uuid.uuid4()),
        ),
    )
    executor.store_result(result.id)
    with sessions.begin() as session:
        session.execute(
            update(JobRecord).where(JobRecord.id == result.id).values(status="succeeded")
        )
    rendered = next(
        service.get_asset(asset_id)
        for asset_id in repo.get_job(result.id).result_asset_ids
        if service.get_asset(asset_id).role == "edit_preview"
    )
    rendered_facts = inspect_video(store.resolve(rendered.storage_key), ffprobe)
    assert rendered_facts["durationFrames"] == 361 and rendered_facts["hasAudio"]
    continued = command.model_copy(
        update={
            "source_result_job_id": result.id,
            "expected_source_timeline_hash": result.frozen_input["timelineHash"],
            "reference_preparation_job_id": None,
            "issue_range": FrameRange(startFrame=330, endFrame=331),
            "include_in_anchor": True,
        }
    )
    next_preview = service.preview_video_repair(project.id, continued)
    assert next_preview.media_input_hash != preview.media_input_hash
    assert next_preview.input_edl.video_segments[-1].asset_id == candidate.id
    next_prep = service.prepare_segment_references(
        project.id, dto.SegmentReferencePreparationCommand(**continued.model_dump())
    )
    resolver.store_reference_preparation(next_prep.id)
    with sessions() as session:
        anchor = session.scalar(
            select(AssetRecord).where(
                AssetRecord.producing_job_id == next_prep.id, AssetRecord.role == "repair_anchor_in"
            )
        )
        with Image.open(store.resolve(anchor.storage_key)) as image:
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
            assert blue > 200 and red < 25
    planning = service.create_video_edit_plan_job(
        project.id,
        dto.VideoEditPlanCommand(**continued.model_dump(), idempotencyKey=str(uuid.uuid4())),
    )
    frames = resolver.prepare_edit_plan_frames(planning.id, planning.frozen_input)
    assert len(frames) == 1
    with Image.open(frames[0]) as image:
        red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert blue > 200 and red < 25
    assert resolver.prepare_edit_plan_frames(planning.id, planning.frozen_input) == frames
    with sessions.begin() as session:
        session.execute(
            update(JobRecord)
            .where(
                JobRecord.project_id == project.id,
                JobRecord.status.not_in(["succeeded", "failed", "cancelled"]),
            )
            .values(status="cancelled")
        )
    engine.dispose()


@pytest.mark.parametrize("duration", [1, 83, 96, 360])
def test_from_frame_uses_last_only_for_full_output_and_actual_reference_limit(duration):
    from dataclasses import replace

    service, repo, project, video, draft, command = edit_fixture()
    service._provider_runtime = replace(
        service._provider_runtime, maximum_segment_image_references=1 if duration in [1, 83] else 2
    )
    target = command.model_copy(
        update={
            "generation_mode": "from_frame",
            "issue_range": FrameRange(startFrame=0, endFrame=duration),
            "anchor_start_frame": 0,
            "anchor_end_frame": duration - 1,
            "audio_mode": "preserve_current",
        }
    )
    preview = service.preview_video_repair(project.id, target)
    assert [ref.role for ref in preview.image_references] == ["first_frame"] + (
        ["last_frame"] if duration in [96, 360] else []
    )
    assert preview.candidate_core_range.duration_frames == duration
    assert preview.provider_duration_seconds * 24 >= duration


def test_canonical_subset_is_frozen_and_stably_ordered():
    service, repo, project, video, draft, command = edit_fixture()
    refs = []
    for index, role in enumerate(["environment", "style_board"]):
        asset = service.register_asset(
            project.id, role=role, media_type="image", sha256=str(index + 1) * 64
        )
        refs.append({"role": role, "assetId": str(asset.id), "sha256": asset.sha256})
    repo._edit_drafts[draft.id] = draft.model_copy(update={"references": refs})
    command = command.model_copy(update={"reference_roles": ["style_board", "environment"]})
    preview = service.preview_video_repair(project.id, command)
    assert [ref.role for ref in preview.image_references] == [
        "anchor_in",
        "environment",
        "style_board",
    ]
    with pytest.raises(dto.StudioConflictError, match="missing"):
        service.preview_video_repair(
            project.id, command.model_copy(update={"reference_roles": ["episode_child"]})
        )
    different = service.preview_video_repair(
        project.id, command.model_copy(update={"reference_roles": ["environment"]})
    )
    assert different.media_input_hash != preview.media_input_hash


def test_custom_context_is_exact_and_cannot_exceed_actual_timeline():
    service, repo, project, video, draft, command = edit_fixture()
    custom = command.model_copy(
        update={"context_mode": "custom", "context_range": FrameRange(startFrame=265, endFrame=361)}
    )
    preview = service.preview_video_repair(project.id, custom)
    assert preview.generation_range == custom.context_range
    assert preview.candidate_core_range == FrameRange(startFrame=13, endFrame=96)
    with pytest.raises(dto.StudioValidationError, match="actual timeline"):
        service.preview_video_repair(
            project.id,
            custom.model_copy(update={"context_range": FrameRange(startFrame=265, endFrame=362)}),
        )


def test_planner_structured_result_lands_without_creating_video_or_story(tmp_path):
    from catflow.infrastructure.media import LocalMediaStore
    from catflow_worker.ark_results import ArkResultLandingService

    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service, repo, project, video, draft, command = edit_fixture(PostgresStudioRepository(sessions))
    job = service.create_video_edit_plan_job(
        project.id,
        dto.VideoEditPlanCommand(**command.model_dump(), idempotencyKey=str(uuid.uuid4())),
    )
    suggestion = {
        "instruction": "保持静止",
        "preserveContent": "光线",
        "startState": "坐着",
        "actionProcess": "持续坐着",
        "desiredEndState": "仍坐着",
        "avoidProblems": "不要移动",
        "recommendedGenerationMode": "edit_existing",
        "recommendedEndStatePolicy": "follow_instruction",
        "recommendedReferenceRoles": [],
        "notes": ["观察事实：当前采样帧主体坐着。"],
    }
    with sessions.begin() as session:
        row = session.get(JobRecord, job.id)
        row.provider_result_json = {"payload": suggestion, "responseId": "test-response"}
    landing = ArkResultLandingService(
        sessions,
        LocalMediaStore(tmp_path / "landing"),
        studio_service=service,
        downloader=None,
        ffprobe_path=tmp_path / "unused",
    )
    landing.store_result(job.id)
    landing.store_result(job.id)
    reloaded = repo.get_job(job.id)
    assert reloaded.provider_result["editPlan"] == suggestion
    assert reloaded.edit_plan.instruction == "保持静止"
    assert [item.kind for item in repo.list_project_jobs(project.id)] == ["plan_video_edit"]
    assert reloaded.result_asset_ids == []
    with sessions.begin() as session:
        session.get(JobRecord, job.id).status = "cancelled"
    engine.dispose()


def test_api_draft_input_conflict_and_explicit_planner_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from catflow.interfaces import api as api_module

    service, repo, project, video, draft, command = edit_fixture()
    monkeypatch.setattr(api_module, "_worker_runtime", lambda _: {"ready": True})
    app = api_module.create_app(
        service,
        settings=api_module.AppSettings(
            csrf_token="test-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            base_url="http://127.0.0.1:8877",
        ),
    )
    headers = {"Origin": "http://127.0.0.1:8877", "X-CatFlow-CSRF": "test-csrf"}
    with TestClient(app) as client:
        path = f"/api/v1/projects/{project.id}/video-edit-drafts/{draft.id}/input"
        body = {
            "expectedRevision": 0,
            "editingInput": {"editContractVersion": 2, "instruction": ""},
        }
        saved = client.patch(path, json=body, headers=headers)
        assert saved.status_code == 200 and saved.json()["inputRevision"] == 1
        assert client.patch(path, json=body, headers=headers).status_code == 409
        body["expectedRevision"] = 1
        body["editingInput"]["baseVideoAssetId"] = str(uuid.uuid4())
        assert client.patch(path, json=body, headers=headers).status_code == 422
        plan = client.post(
            f"/api/v1/projects/{project.id}/video-edits/plans",
            json={
                **command.model_dump(mode="json", by_alias=True),
                "idempotencyKey": "explicit-api-planner",
            },
            headers=headers,
        )
        assert plan.status_code == 202
        assert plan.json()["kind"] == "plan_video_edit"
        assert plan.json()["frozenInput"]["command"]["instruction"] == command.instruction
        assert len(repo.list_project_jobs(project.id)) == 1
        assert "VideoEditPlanSuggestion" in app.openapi()["components"]["schemas"]


def test_migration_adds_only_input_columns_and_planner_job_kind():
    import importlib.util
    from io import StringIO
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        Path(__file__).resolve().parents[2]
        / "services/api/alembic/versions/0034_video_edit_input.py"
    )
    spec = importlib.util.spec_from_file_location("video_edit_input_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0033_import_confirm_snapshot"
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "ADD COLUMN editing_input_json JSONB" in sql
    assert "ADD COLUMN input_revision INTEGER" in sql
    assert "plan_video_edit" in sql
    assert "UPDATE " not in sql and "DELETE " not in sql


def test_memory_concurrent_input_and_head_save_preserve_both_revisions(monkeypatch):
    from concurrent.futures import TimeoutError
    from threading import Event

    service, repo, project, video, draft, command = edit_fixture()
    parent = repo.get_edit(draft.head_edit_version_id)
    edit = parent.model_copy(
        update={
            "id": uuid.uuid4(),
            "parent_edit_version_id": parent.id,
            "save_request_hash": "f" * 64,
        }
    )
    entered, release, input_started = Event(), Event(), Event()
    original_get_edit = repo.get_edit

    def paused_get_edit(edit_id):
        if edit_id == parent.id:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release save")
        return original_get_edit(edit_id)

    monkeypatch.setattr(repo, "get_edit", paused_get_edit)

    def update_input():
        input_started.set()
        return repo.update_video_edit_draft_input(draft.id, 0, {"instruction": "new text"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        head_future = pool.submit(repo.save_video_draft_revision, edit, parent.timeline_hash, None)
        assert entered.wait(5)
        input_future = pool.submit(update_input)
        assert input_started.wait(5)
        try:
            with pytest.raises(TimeoutError):
                input_future.result(timeout=0.1)
        finally:
            release.set()
        head_future.result(timeout=5)
        input_future.result(timeout=5)
    stored = repo.get_video_edit_draft(draft.id)
    assert stored.head_edit_version_id == edit.id
    assert stored.input_revision == 1
    assert stored.editing_input == {"instruction": "new text"}
