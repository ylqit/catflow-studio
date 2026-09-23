"""New production behavior, not golden copies of the implementation."""
import uuid
from dataclasses import replace

import pytest
from pydantic import ValidationError

from catflow.application.provider_config import ProviderRuntime
from catflow.application.production_plan import ProductionPlanDraft, UnitGeneration
from catflow.application.production_selection import UnitSelectionCommand, ProductionAssembly
from catflow.application.service import CanonRevisionCreateCommand, ProjectCreate, StoryCreateCommand, StudioService, StudioConflictError
from catflow.domain.models import LifeClipSpec, ShotPlanDraft, ShotSpec, ActionBeat
from catflow.domain.narrative import NarrativeDesign
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def ready(duration=45, repository=None):
    repository = repository or MemoryStudioRepository()
    service = StudioService(repository, provider_runtime=replace(
        ProviderRuntime.from_env(segment_reference_publishing_ready=False), paid_calls_enabled=True))
    if not service.get_canon(repository.active_canon_profile_id()).fixed_assets:
        references = {role: service.register_canon_asset(role=role, sha256=f"{i:x}"*64,
            storage_key=f"canon/{role}.png", byte_size=100).id
            for i,role in enumerate(("episode_child","episode_cat","pair_scale","style_board"),1)}
        service.publish_canon_revision(CanonRevisionCreateCommand(fixedAssets=references))
    project = service.create_project(ProjectCreate(title="软垫与纸箱", theme="猫选择纸箱", targetDurationSeconds=duration))
    narrative = NarrativeDesign(characterGoal="孩子布置软垫", audienceExpectation="猫会坐软垫",
        smallDisruption="猫看向纸箱", reveal="猫坐在纸箱里", response="孩子挪近纸箱", payoff="陪伴猫的选择")
    story = service.create_story(project.id, StoryCreateCommand(title=project.title, body=project.theme,
        targetDurationSeconds=duration, dialoguePolicy="none", environmentIntent="客厅",
        narrativeDesign=narrative, microEvent={"trigger":"铺软垫", "childAction":"邀请猫咪",
            "catResponse":"选择纸箱", "visibleChange":"纸箱里出现猫咪", "warmEnding":"挪近纸箱"}))
    env = service.register_asset(project.id, role="environment", sha256="a"*64)
    service.select_asset(project.id, slot="environment", asset_id=env.id)
    box = service.register_asset(project.id, role="prop", sha256="b"*64)
    lengths = [120]*(duration//5)
    if duration%5: lengths.append((duration%5)*24)
    if lengths[-1] < 24: raise ValueError("fixture duration")
    shots = [ShotSpec(id=f"s{i+1}", order=i+1, formatVersion=2, durationSeconds=frames/24,
        durationFrames=frames, framing="纸箱特写", cameraMovement="固定", childAction="", catAction="",
        environmentChange="纸箱有轻微形变", transition="hard_cut", information={"role":"reveal",
            "newInformation":"纸箱里有猫", "visibleSubjects":["prop"], "propKeys":["box"],
            "keyEvents":[{"id":"reveal", "description":"纸箱形变", "startFrame":0, "endFrame":frames}]},
        actionBeats=[ActionBeat(startFrame=0, endFrame=frames, purpose="reaction", childAction="", catAction="",
            environmentAction="箱壁轻微变形", visibleChange="箱壁向外轻鼓")]) for i,frames in enumerate(lengths)]
    plan = service.create_shot_plan(project.id, ShotPlanDraft(sourceStoryVersionId=story.id,
        sourceSelectionHash=service.current_selection_hash(project.id), clip=LifeClipSpec(
            formatVersion=2, durationSeconds=duration, aspectRatio="9:16", microEvent="选纸箱", childAction="铺垫",
            catActionOrObservation="选纸箱", visibleCauseAndEffect="挪近纸箱", warmEnding="陪伴", dialoguePolicy="none", environmentIntent="客厅"), shots=shots))
    command = ProductionPlanDraft(shotPlanVersionId=plan.id,idempotencyKey=f"plan-{uuid.uuid4()}",
        props=[{"key":"box","name":"旧纸箱","assetId":box.id,"identity":"一个棕色方箱", "initialState":"空箱", "location":"软垫旁"}])
    production = service.production.create_plan(project.id,command)
    service.production.repository.activate_production_plan(project.id,production.id,None)
    return service,project,production,command


@pytest.mark.parametrize("duration",[8,12,15,30,45,60])
def test_work_duration_is_separate_from_legal_unit_duration(duration):
    service,project,plan,command=ready(duration)
    assert sum(round(s["durationSeconds"]*24) for s in plan.document["shots"]) == duration*24
    units=plan.document["units"]
    assert len(units)==(duration+14)//15
    preview=service.production.preview_unit(project.id,units[0]["id"],plan.id)
    assert 4<=preview["durationSeconds"]<=15
    assert "箱壁轻微变形" in preview["compiledProviderPrompt"]
    assert "prop:box" in preview["referenceRoles"]
    assert len(preview["references"])==6
    assert all("three" not in r for r in preview["referenceRoles"])
    assert service.production.create_plan(project.id,command).id==plan.id
    if duration>15:
        with pytest.raises(StudioConflictError): service.preview_video_generation(project.id)
        with pytest.raises(StudioConflictError,match="上游"):
            service.production.preview_unit(project.id,units[1]["id"],plan.id)


def test_selection_checks_observed_facts_and_assembles_exact_frames():
    service,project,plan,_=ready(15)
    unit=plan.document["units"][0]["id"]
    preview=service.production.preview_unit(project.id,unit,plan.id)
    cmd=UnitGeneration(planId=plan.id,expectedInputHash=preview["inputHash"],idempotencyKey="unit-test-generation")
    job=service.production.generate_unit(project.id,unit,cmd)
    assert service.production.generate_unit(project.id,unit,cmd).id==job.id
    with pytest.raises(StudioConflictError,match="运行"):
        service.production.generate_unit(project.id,unit,cmd.model_copy(update={"idempotency_key":"different-generation"}))
    video=service.register_asset(project.id,role="production_unit_video",media_type="video",sha256="c"*64,
        producing_job_id=job.id, metadata={"unitDesignHash":preview["unitDesignHash"], "productionUnitId":unit,
            "frameRateNumerator":24,"frameRateDenominator":1,"durationFrames":360})
    evidence=service.register_asset(project.id,role="unit_end_frame",sha256="d"*64,metadata={
        "sourceVideoAssetId":str(video.id),"sourceVideoSha256":video.sha256,"sourceFrame":359})
    selection=UnitSelectionCommand(planId=plan.id,expectedDesignHash=preview["unitDesignHash"],assetId=video.id,
        takes=[{"shotId":s["id"],"sourceInFrame":i*120,"durationFrames":120} for i,s in enumerate(plan.document["shots"])],
        events=[{"shotId":s["id"],"eventId":"reveal","verdict":"pass","sourceStartFrame":i*120,"sourceEndFrame":(i+1)*120} for i,s in enumerate(plan.document["shots"])],
        endState={"facts":[{"key":"prop:box","value":"箱壁向外轻鼓","certainty":"observed"}],
            "evidenceFrame":359,"evidenceAssetId":evidence.id,"confirmed":True},
        disposition="accepted",audioPolicy="mute",idempotencyKey="unit-test-selection")
    invalid=selection.model_copy(deep=True); invalid.takes[1].source_in_frame=119
    with pytest.raises(ValueError,match="重叠"): service.production.select_unit(project.id,unit,invalid)
    chosen=service.production.select_unit(project.id,unit,selection)
    assembly=ProductionAssembly(expectedSelectionHashes={unit:chosen.input_hash},idempotencyKey="unit-test-assembly")
    draft=service.production.assemble(project.id,plan.id,assembly)
    assert service.production.assemble(project.id,plan.id,assembly).id==draft.id
    edit=service.production.repository._edits[project.id][-1]
    assert edit.edl.total_frames==360
    assert len(edit.edl.video_segments)==3
    assert all(segment.muted for segment in edit.edl.audio.segments)


def test_legacy_serialization_and_new_frame_validation():
    legacy=ShotSpec(id="old",order=1,durationSeconds=4,framing="中景",cameraMovement="固定",childAction="坐下",catAction="看",environmentChange="风",transition="hard_cut")
    dumped=legacy.model_dump(by_alias=True)
    assert "formatVersion" not in dumped and "information" not in dumped
    assert isinstance(dumped["durationSeconds"],int)
    with pytest.raises(ValidationError): ProjectCreate(title="bad",theme="x",targetDurationSeconds=61)
    with pytest.raises(ValidationError): ShotSpec(**{**dumped,"formatVersion":2,"durationFrames":24})


def test_postgres_serializes_duplicate_paid_submission_and_restores_plans():
    from concurrent.futures import ThreadPoolExecutor
    from catflow.infrastructure.database import DatabaseSettings, create_database_engine, create_session_factory
    from catflow.infrastructure.postgres_repository import PostgresStudioRepository
    engine=create_database_engine(DatabaseSettings.from_env())
    sessions=create_session_factory(engine)
    try:
        service,project,plan,_=ready(45,PostgresStudioRepository(sessions))
        unit=plan.document["units"][0]["id"]
        preview=service.production.preview_unit(project.id,unit,plan.id)
        def submit(index):
            try:
                return service.production.generate_unit(project.id,unit,UnitGeneration(planId=plan.id,
                    expectedInputHash=preview["inputHash"],idempotencyKey=f"concurrent-{project.id}-{index}"))
            except StudioConflictError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(submit,[1,2]))
        assert sum(result is not None for result in results)==1
        reloaded=StudioService(PostgresStudioRepository(sessions))
        assert reloaded.production.repository.get_production_plan(plan.id).document==plan.document
        assert reloaded.production.preview_unit(project.id,unit,plan.id)["inputHash"]==preview["inputHash"]
        assert reloaded.list_stories(project.id)[0].narrative_design.character_goal=="孩子布置软垫"
    finally:
        engine.dispose()
