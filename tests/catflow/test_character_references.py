from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

from catflow.application.character_references import (
    CharacterRemakeCommand,
    CharacterRemakePreviewCommand,
    ReferenceBindingCommand,
)
from catflow.application.series import SeriesCreateCommand
from catflow.application.service import (
    JobDto,
    PlannerMessageCommand,
    ProjectCreate,
    StudioConflictError,
    StudioService,
)
from catflow.infrastructure.cat_reference_catalog import initialize_cat_reference_catalog
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


@pytest.fixture(params=["memory", "postgres"])
def studio(request, tmp_path_factory):
    tmp_path = tmp_path_factory.getbasetemp() / "catalog-media"
    engine = (
        create_database_engine(DatabaseSettings.from_env()) if request.param == "postgres" else None
    )
    repository = (
        PostgresStudioRepository(create_session_factory(engine))
        if engine
        else MemoryStudioRepository()
    )
    repository.active_canon_profile_id()
    default_id = repository.active_canon_profile_id()
    initialize_cat_reference_catalog(
        repository, LocalMediaStore(tmp_path), Path(__file__).resolve().parents[2]
    )
    assert repository.active_canon_profile_id() == default_id
    service = StudioService(repository)
    options = {option.key: option for option in service.cat_reference_options()}
    assert all(option.available for option in options.values())
    yield service, repository, options
    if engine:
        engine.dispose()


def project(service, canon):
    return service.create_project(
        ProjectCreate(
            title="角色选择验证",
            theme="夏天在家陪伴",
            targetDurationSeconds=15,
            canonProfileId=canon,
        )
    )


def job(project, canon, status="queued"):
    now = datetime.now(UTC)
    return JobDto(
        id=uuid.uuid4(),
        projectId=project.id,
        kind="plan_story",
        status=status,
        inputHash="a" * 64,
        idempotencyKey=str(uuid.uuid4()),
        provider="mock",
        model="mock",
        frozenInput={"canonProfileId": str(canon)},
        createdAt=now,
        updatedAt=now,
    )


def test_catalog_and_draft_switch_preserve_default_and_pair(studio):
    service, repository, options = studio
    gray, white = (options[k] for k in ("gray-original", "white-v4"))
    current = repository.active_canon_profile_id()
    p = project(service, gray.canon_profile_id)
    assert service.reference_binding("project", p.id).can_change
    command = ReferenceBindingCommand(
        canonProfileId=white.canon_profile_id, expectedCanonProfileId=p.canon_profile_id
    )
    assert service.change_reference_binding("project", p.id, command).label == "V4 校色白猫"
    assert (
        service.change_reference_binding("project", p.id, command).canon_profile_id
        == white.canon_profile_id
    )
    selected = service.current_selections(p.id)
    assert selected["episode_cat"].sha256 == white.fixed_assets["episode_cat"].sha256
    assert selected["pair_scale"].sha256 == white.fixed_assets["pair_scale"].sha256
    assert selected["episode_child"].sha256 == gray.fixed_assets["episode_child"].sha256
    assert set(selected) == {"episode_child", "episode_cat", "pair_scale", "style_board"}
    assert {a.view for a in white.auxiliary} == {"front", "rear", "three-view"}
    assert repository.active_canon_profile_id() == current


@pytest.mark.parametrize("status", ["queued", "failed", "cancelled", "submission_unknown"])
def test_production_never_unlocks_when_job_fails_or_cancels(studio, status):
    service, repository, options = studio
    p = project(service, options["gray-original"].canon_profile_id)
    queued = job(p, p.canon_profile_id, status)
    repository.create_job(queued)
    assert not service.reference_binding("project", p.id).can_change
    with pytest.raises(StudioConflictError):
        service.change_reference_binding(
            "project",
            p.id,
            ReferenceBindingCommand(
                canonProfileId=options["white-v4"].canon_profile_id,
                expectedCanonProfileId=p.canon_profile_id,
            ),
        )
    assert repository.create_job(queued).id == queued.id


def test_switch_racing_enqueue_has_only_one_winner(studio):
    service, repository, options = studio
    p = project(service, options["gray-original"].canon_profile_id)
    white_id = options["white-v4"].canon_profile_id
    barrier = Barrier(2)

    def change():
        barrier.wait()
        try:
            service.change_reference_binding(
                "project",
                p.id,
                ReferenceBindingCommand(
                    canonProfileId=white_id, expectedCanonProfileId=p.canon_profile_id
                ),
            )
            return "changed"
        except StudioConflictError:
            return "conflict"

    def enqueue():
        barrier.wait()
        try:
            repository.create_job(job(p, p.canon_profile_id))
            return "enqueued"
        except StudioConflictError:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(change), pool.submit(enqueue)
        results = {a.result(), b.result()}
    assert results in ({"changed", "conflict"}, {"enqueued", "conflict"})
    binding = service.reference_binding("project", p.id)
    assert binding.canon_profile_id == (white_id if "changed" in results else p.canon_profile_id)


@pytest.mark.parametrize("scope", ["project", "series"])
def test_remake_is_local_idempotent_and_keeps_input(studio, scope):
    service, repository, options = studio
    gray, white = (options[k].canon_profile_id for k in ("gray-original", "white-v4"))
    source = (
        project(service, gray)
        if scope == "project"
        else service.create_story_series(
            SeriesCreateCommand(
                title="暑假",
                premise="冰棒、风扇、画画",
                narrativeMode="anthology",
                plannedEpisodeCount=3,
                defaultEpisodeDurationSeconds=15,
                worldSetting="夏天在家",
                emotionalDirection="温馨",
                adaptationPolicy="condense_mainline",
                additionalNotes="每集独立，不继承道具",
                canonProfileId=gray,
            )
        )
    )
    command = CharacterRemakePreviewCommand(
        sourceType=scope, sourceId=source.id, canonProfileId=white, title="暑假白猫版"
    )
    preview = service.preview_character_remake(command)
    create = CharacterRemakeCommand(
        **command.model_dump(),
        expectedInputHash=preview.input_hash,
        idempotencyKey=str(uuid.uuid4()),
    )
    first = service.create_character_remake(create)
    second = service.create_character_remake(create)
    assert first.id == second.id and first.target_id != source.id
    assert service.reference_binding(scope, source.id).canon_profile_id == gray
    assert service.reference_binding(scope, first.target_id).can_change
    assert service.reference_binding(scope, first.target_id).canon_profile_id == white
    target = (
        service.get_project(first.target_id)
        if scope == "project"
        else service.get_story_series(first.target_id)
    )
    assert target.title == "暑假白猫版"
    if scope == "series":
        assert (
            target.narrative_mode == "anthology" and target.default_episode_duration_seconds == 15
        )
        assert target.additional_notes == source.additional_notes
    changed = create.model_copy(update={"title": "另一个名称"})
    with pytest.raises(StudioConflictError):
        service.create_character_remake(changed)


def test_stale_remake_preview_is_rejected(studio):
    service, _, options = studio
    p = project(service, options["gray-original"].canon_profile_id)
    command = CharacterRemakePreviewCommand(
        sourceType="project", sourceId=p.id, canonProfileId=options["white-v4"].canon_profile_id
    )
    with pytest.raises(StudioConflictError):
        service.create_character_remake(
            CharacterRemakeCommand(
                **command.model_dump(), expectedInputHash="0" * 64, idempotencyKey=str(uuid.uuid4())
            )
        )


def test_planner_freezes_selected_identity_and_references(studio):
    service, _, options = studio
    white = options["white-v4"]
    p = project(service, white.canon_profile_id)
    queued = service.enqueue_planner_message(
        p.id,
        PlannerMessageCommand(
            text="猫咪陪孩子画画", expectedContextRevision=1, idempotencyKey=str(uuid.uuid4())
        ),
    )
    assert queued.frozen_input["canonProfileId"] == str(white.canon_profile_id)
    assert "V4白猫" in queued.frozen_input["catIdentity"]
    assert "V4白猫" in queued.frozen_input["prompt"]
    assert set(queued.frozen_input["fixedCanonReferences"]) == {
        "episode_child",
        "episode_cat",
        "pair_scale",
        "style_board",
    }


def test_import_choice_replay_existing_target_conflict_and_remake_sources(studio):
    from catflow.application.story_imports import (
        StoryImportAnalysisDraft,
        StoryImportConfirmCommand,
        StoryImportCreateCommand,
        StoryImportPreviewCommand,
        StoryProductionTargetsCommand,
    )

    service, repository, options = studio
    gray, white = (options[k].canon_profile_id for k in ("gray-original", "white-v4"))
    raw = "孩子打开冰箱，灰猫坐在旁边。孩子取出冰棒，灰猫陪伴。"
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=raw, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=raw,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey=str(uuid.uuid4()),
        )
    )
    doc = service.complete_story_import_analysis(
        created.analysis_job.id,
        StoryImportAnalysisDraft.model_validate(
            {
                "units": [{"ordinal": 1, "title": "冰棒", "rawText": raw}],
                "relationSuggestions": [
                    {
                        "relationType": "new_series",
                        "unitOrdinals": [1],
                        "title": "暑假",
                        "narrativeMode": "anthology",
                        "confidence": 95,
                        "rationale": "独立日常",
                    }
                ],
            }
        ),
    )
    goal = {
        "canonProfileId": str(white),
        "lengthMode": "fixed",
        "plannedEpisodeCount": 3,
        "defaultEpisodeDurationSeconds": 15,
        "narrativeMode": "anthology",
        "adaptationPolicy": "condense_mainline",
        "mustKeep": [],
    }
    updated = service.update_story_production_targets(
        doc.id,
        StoryProductionTargetsCommand(
            expectedUpdatedAt=doc.updated_at, productionTargets={"default": goal}
        ),
    )
    assert updated.analysis_job_id == doc.analysis_job_id
    assert service.get_story_import(doc.id).production_targets["default"].canon_profile_id == white
    command = StoryImportConfirmCommand(
        suggestionId=doc.relation_suggestions[0].id,
        target="new_series",
        canonProfileId=white,
        seriesLengthMode="fixed",
        plannedEpisodeCount=3,
        narrativeMode="anthology",
        defaultEpisodeDurationSeconds=15,
        adaptationPolicy="condense_mainline",
        idempotencyKey=str(uuid.uuid4()),
    )
    result = service.confirm_story_import(doc.id, command)
    assert result.series.canon_profile_id == white
    assert service.confirm_story_import(doc.id, command).series.id == result.series.id
    assert service.reference_binding("series", result.series.id).can_change
    with pytest.raises(StudioConflictError):
        service.confirm_story_import(
            doc.id,
            StoryImportConfirmCommand(
                suggestionId=command.suggestion_id,
                target="append_series",
                targetSeriesId=result.series.id,
                canonProfileId=gray,
                idempotencyKey=str(uuid.uuid4()),
            ),
        )
    remake = CharacterRemakePreviewCommand(
        sourceType="series", sourceId=result.series.id, canonProfileId=gray
    )
    planned = service.preview_character_remake(remake)
    assert planned.source_snapshot["sources"][0]["rawText"] == raw
    remade = service.create_character_remake(
        CharacterRemakeCommand(
            **remake.model_dump(),
            expectedInputHash=planned.input_hash,
            idempotencyKey=str(uuid.uuid4()),
        )
    )
    assert (
        repository.character_remake_source("series", remade.target_id)["sources"][0]["rawText"]
        == raw
    )


def test_series_episode_inherits_and_cannot_override(studio):
    from catflow.application.series import (
        SeriesEpisodeMaterializeCommand,
        SeriesPlanActivationCommand,
        SeriesPlanGenerationCommand,
    )
    from test_series_workflow import _plan, _series_command

    service, _, options = studio
    gray, white = (options[k].canon_profile_id for k in ("gray-original", "white-v4"))
    series = service.create_story_series(
        _series_command().model_copy(update={"canon_profile_id": gray})
    )
    service.change_reference_binding(
        "series",
        series.id,
        ReferenceBindingCommand(canonProfileId=white, expectedCanonProfileId=gray),
    )
    preview = service.preview_series_plan(series.id)
    assert "V4白猫" in preview.prompt
    queued = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash, idempotencyKey=str(uuid.uuid4())
        ),
    )
    candidate = service.complete_series_plan_job(queued.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None, idempotencyKey=str(uuid.uuid4())
        ),
    )
    episode = service.list_series_episodes(series.id)[0]
    p = service.materialize_series_episode(
        series.id, episode.id, SeriesEpisodeMaterializeCommand(idempotencyKey=str(uuid.uuid4()))
    )
    assert p.canon_profile_id == white
    assert service.reference_binding("project", p.id).owner_series_id == series.id
    for scope, oid in (("series", series.id), ("project", p.id)):
        with pytest.raises(StudioConflictError):
            service.change_reference_binding(
                scope,
                oid,
                ReferenceBindingCommand(canonProfileId=gray, expectedCanonProfileId=white),
            )


def test_provider_receives_white_master_and_pair_without_auxiliary(tmp_path):
    import base64
    import hashlib
    from types import SimpleNamespace

    from catflow_worker.ark_gateway import ArkTypedGateway
    from test_ark_gateway import Recorder, _image, _settings

    repository = MemoryStudioRepository()
    media = LocalMediaStore(tmp_path)
    initialize_cat_reference_catalog(repository, media, Path(__file__).resolve().parents[2])
    service = StudioService(repository)
    white = next(o for o in service.cat_reference_options() if o.key == "white-v4")
    p = project(service, white.canon_profile_id)
    selected = service.current_selections(p.id)
    environment = _image(tmp_path / "environment.png", "beige")
    roles = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
    paths = tuple(
        environment
        if role == "environment"
        else media.resolve(repository.get_asset(selected[role].id).storage_key)
        for role in roles
    )
    recorder = Recorder(SimpleNamespace(id="mock-white-video"))
    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(content_generation=SimpleNamespace(tasks=recorder))
    )
    gateway.submit_video(
        prompt=white.cat_identity,
        reference_paths=paths,
        reference_roles=roles,
        duration_seconds=15,
        resolution="480p",
    )
    request = recorder.calls[0]
    images = [item for item in request["content"] if item["type"] == "image_url"]
    hashes = [
        hashlib.sha256(base64.b64decode(item["image_url"]["url"].split(",", 1)[1])).hexdigest()
        for item in images
    ]
    assert len(images) == 5
    assert hashes[1] == white.fixed_assets["episode_cat"].sha256
    assert hashes[2] == white.fixed_assets["pair_scale"].sha256
    assert not set(hashes).intersection(a.asset.sha256 for a in white.auxiliary)
