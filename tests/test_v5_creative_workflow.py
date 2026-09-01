from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from cat_video_generator.application.ports import (
    DirectorResult,
    StoredProject,
    StoredScene,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
)
from cat_video_generator.application.shot_queue import (
    ProjectEditingService,
    RevisionConflictError,
)
from cat_video_generator.domain.contracts import (
    AcceptedVisualAssetPlan,
    SceneDraft,
    ShotAssistAnalysis,
    ShotCardDraft,
    ShotPromptContext,
    StoryDiagnosisOutput,
    StoryRewriteOutput,
    VisualAssetAction,
    VisualAssetPlanSelection,
    VisualProfileDraft,
)
from cat_video_generator.domain.creative_workflow import story_source_hash
from cat_video_generator.domain.prompts import (
    compile_shot_assistance_prompt,
    compile_shot_video_prompt_parts,
    compile_story_diagnosis_prompt,
    compile_story_rewrite_prompt,
)
from cat_video_generator.domain.rendering import MediaSource, build_shot_input_plan
from cat_video_generator.domain.shot_assistance import analyze_shot_draft
from cat_video_generator.domain.workflow import RunStatus, SceneStatus, StepKind, StepStatus


def _diagnosis_payload() -> dict[str, object]:
    return {
        "overallAssessment": "核心事件明确，但动作起点与道具流向需要统一。",
        "issues": [
            {
                "category": "physical_feasibility",
                "evidence": "原文中的长道具与容器结构关系不清楚。",
                "impact": "生成时可能出现穿透或物体突然出现。",
                "suggestion": "改为合理收纳形态，并明确取出路径。",
            }
        ],
        "rewriteOptions": [
            {
                "strategy": "conservative",
                "title": "保守修订",
                "summary": "只修正空间和连续性。",
                "tradeoffs": "最大程度保留原文，但创作变化较少。",
            },
            {
                "strategy": "balanced",
                "title": "平衡优化",
                "summary": "调整动作与事件顺序。",
                "tradeoffs": "会改写部分动作，但稳定性更高。",
            },
            {
                "strategy": "creative",
                "title": "创作增强",
                "summary": "重新组织事件并增强人猫互动。",
                "tradeoffs": "变化最大，需要更多人工校对。",
            },
        ],
    }


def _rewrite_payload() -> dict[str, object]:
    return {
        "rewrittenStory": "孩子整理好装备，猫咪观察并跟随，动作从准备自然过渡到出门。",
        "changeSummary": ["统一道具流向", "减少重复动作"],
        "unresolvedQuestions": ["是否保留最后的回头互动"],
    }


def test_story_stage_contracts_are_strict_and_require_three_distinct_strategies() -> None:
    diagnosis = StoryDiagnosisOutput.model_validate(_diagnosis_payload())
    rewrite = StoryRewriteOutput.model_validate(_rewrite_payload())

    assert [item.strategy.value for item in diagnosis.rewrite_options] == [
        "conservative",
        "balanced",
        "creative",
    ]
    assert rewrite.rewritten_story.startswith("孩子")

    invalid = _diagnosis_payload()
    invalid["rewriteOptions"] = list(invalid["rewriteOptions"])[:2]
    with pytest.raises(ValueError, match="at least 3|three rewrite strategies"):
        StoryDiagnosisOutput.model_validate(invalid)


def test_story_role_prompts_use_context_without_story_specific_case_rules() -> None:
    scene = SceneDraft(
        title="出门准备",
        sourceText="孩子和猫咪整理装备后出门。",
        contextNote="上一场刚结束早餐。",
        storyMode="multi",
        targetShotCount=4,
    )
    profile = VisualProfileDraft()

    diagnosis_prompt = compile_story_diagnosis_prompt(
        project_title="湖边日常",
        scene=scene,
        visual_profile=profile,
        previous_scene_summary="早餐结束。",
        next_scene_summary="抵达目的地。",
    )
    rewrite_prompt = compile_story_rewrite_prompt(
        project_title="湖边日常",
        scene=scene,
        visual_profile=profile,
        accepted_diagnosis={
            "selectedStrategy": "balanced",
            "additionalInstructions": "保留温和节奏",
            "diagnosis": _diagnosis_payload(),
        },
    )

    assert "剧情医生" in diagnosis_prompt
    assert "保守修订、平衡优化、创作增强" in diagnosis_prompt
    assert "【目标视频片段数量】4" in diagnosis_prompt
    assert "剧本编辑" in rewrite_prompt
    assert "selectedStrategy" in rewrite_prompt
    for case_word in ("鱼竿", "柜子", "鱼饵盒"):
        assert case_word not in diagnosis_prompt
        assert case_word not in rewrite_prompt


class _Gateway:
    model = "fake-planner"
    analysis_model = "fake-multimodal"

    def __init__(self) -> None:
        self.payload: dict[str, object] = _diagnosis_payload()
        self.calls: list[tuple[str, str]] = []

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        output_name: str,
    ) -> DirectorResult:
        self.calls.append((output_name, prompt))
        return DirectorResult(
            payload=self.payload,
            response_id=f"response-{len(self.calls)}",
            model=self.model,
            request_hash=f"request-{len(self.calls)}",
        )


class _Repository:
    def __init__(self) -> None:
        self.project = StoredProject(
            id=uuid.uuid4(),
            title="湖边日常",
            content_date=date(2026, 8, 13),
            status=RunStatus.ACTIVE,
        )
        self.scene = StoredScene(
            id=uuid.uuid4(),
            project_id=self.project.id,
            order=1,
            draft=SceneDraft(
                title="出门准备",
                sourceText="孩子和猫咪整理装备后出门。",
                storyMode="multi",
                targetShotCount=4,
            ),
            status=SceneStatus.DRAFT,
        )
        self.profile = StoredVisualProfileRevision(
            id=uuid.uuid4(),
            project_id=self.project.id,
            revision=1,
            profile_hash="profile-hash",
            source_profile_id="Canon-v1",
            draft=VisualProfileDraft(),
        )
        self.steps: list[StoredStep] = []
        self.restored_profile: VisualProfileDraft | None = None

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        assert scene_id == self.scene.id
        return self.scene

    def get_project(self, project_id: uuid.UUID) -> StoredProject:
        assert project_id == self.project.id
        return self.project

    def get_visual_profile(self, project_id: uuid.UUID) -> StoredVisualProfileRevision:
        assert project_id == self.project.id
        return self.profile

    def get_default_visual_profile(self, project_id: uuid.UUID) -> VisualProfileDraft:
        assert project_id == self.project.id
        return VisualProfileDraft(
            referenceBindings=[
                {"assetId": str(uuid.uuid4()), "purpose": "person_identity"},
                {"assetId": str(uuid.uuid4()), "purpose": "cat_identity"},
                {"assetId": str(uuid.uuid4()), "purpose": "style"},
            ]
        )

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> tuple[StoredVisualProfileRevision, int]:
        assert project_id == self.project.id
        self.restored_profile = draft
        self.profile = replace(
            self.profile,
            id=uuid.uuid4(),
            revision=self.profile.revision + 1,
            draft=draft,
        )
        return self.profile, 2

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]:
        assert project_id == self.project.id
        return (self.scene,)

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        assert scene_id == self.scene.id
        return ()

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]:
        assert project_id == self.project.id
        return tuple(
            item
            for item in self.steps
            if (scene_id is None or item.scene_id == scene_id)
            and (shot_id is None or item.shot_card_id == shot_id)
        )

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int:
        return 1 + sum(
            item.scene_id == scene_id and item.operation_key == operation_key
            for item in self.steps
        )

    def create_step_with_prompt(self, **values: object) -> tuple[StoredStep, object]:
        step = StoredStep(
            id=uuid.uuid4(),
            project_id=values["project_id"],
            scene_id=values["scene_id"],
            shot_card_id=values["shot_id"],
            kind=values["kind"],
            status=StepStatus.PENDING,
            attempt=values["attempt"],
            operation_key=values["operation_key"],
            input_snapshot=values["input_snapshot"],
            provider=values["provider"],
            model=values["model"],
            created_at=datetime.now(UTC),
        )
        self.steps.append(step)
        return step, object()

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, object] | None = None,
        input_snapshot: dict[str, object] | None = None,
    ) -> StoredStep:
        index = next(index for index, item in enumerate(self.steps) if item.id == step_id)
        step = replace(
            self.steps[index],
            status=status,
            error=error,
            input_snapshot=(
                self.steps[index].input_snapshot
                if input_snapshot is None
                else input_snapshot
            ),
        )
        self.steps[index] = step
        return step

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        return next(item for item in self.steps if item.id == step_id)

    def accept_story_diagnosis(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, object],
    ) -> StoredStep:
        assert expected_source_hash == story_source_hash(self.scene.draft)
        step = self.get_step(step_id)
        updated = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        self.steps[self.steps.index(step)] = updated
        return updated

    def accept_story_rewrite(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, object],
        rewritten_story: str,
    ) -> StoredScene:
        assert expected_source_hash == story_source_hash(self.scene.draft)
        step = self.get_step(step_id)
        self.steps[self.steps.index(step)] = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        self.scene = replace(
            self.scene,
            draft=self.scene.draft.model_copy(update={"source_text": rewritten_story}),
        )
        return self.scene


def test_story_diagnosis_requires_payment_and_keeps_provider_and_accepted_drafts() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )

    with pytest.raises(ValueError, match="explicit paid-generation"):
        service.diagnose_story(repository.scene.id, allow_paid_generation=False)
    assert gateway.calls == []

    result = service.diagnose_story(
        repository.scene.id,
        allow_paid_generation=True,
    )
    edited = result.output.model_copy(
        update={"overall_assessment": "人工补充后的总体评价。"}
    )
    service.accept_story_diagnosis(
        result.step_id,
        diagnosis=edited,
        selected_strategy="balanced",
        additional_instructions="保留温和的人猫互动",
        preserve_original=False,
    )

    saved = repository.get_step(result.step_id).input_snapshot
    assert saved["providerOutput"]["overallAssessment"].startswith("核心事件")
    assert saved["acceptedOutput"]["diagnosis"]["overallAssessment"].startswith("人工补充")
    assert saved["acceptedOutput"]["selectedStrategy"] == "balanced"
    assert saved["acceptedAt"]
    assert repository.scene.draft.source_text == "孩子和猫咪整理装备后出门。"


def test_manual_visual_asset_plan_revision_is_versioned_without_provider_call() -> None:
    class RevisionRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.storyboard_revision_id = uuid.uuid4()
            self.generation_plan_id = uuid.uuid4()
            self.revision_call: dict[str, object] | None = None

        def storyboard_production_context(self, scene_id: uuid.UUID) -> dict[str, object]:
            assert scene_id == self.scene.id
            return {
                "structureApproved": True,
                "generationPlanApproved": True,
                "storyboardRevisionId": str(self.storyboard_revision_id),
                "structureHash": "structure-hash",
                "generationPlanId": str(self.generation_plan_id),
                "generationPlanHash": "generation-plan-hash",
            }

        def list_assets(self, **_values: object) -> tuple[object, ...]:
            return ()

        def revise_visual_asset_plan(self, **values: object) -> StoredStep:
            self.revision_call = values
            source = self.get_step(values["step_id"])  # type: ignore[arg-type]
            revised = replace(
                source,
                id=uuid.uuid4(),
                attempt=source.attempt + 1,
                provider="manual",
                model="human-editor",
                input_snapshot={
                    **source.input_snapshot,
                    "source": "manual",
                    "manualRevisionOfStepId": str(source.id),
                    "manualRevisionNote": values["note"],
                    "acceptedOutput": values["accepted_output"].model_dump(  # type: ignore[union-attr]
                        mode="json", by_alias=True
                    ),
                },
            )
            self.steps.append(revised)
            return revised

    repository = RevisionRepository()
    unavailable_planning_reference_id = uuid.uuid4()
    provider_output = {
        "overallAssessment": "只保留真正需要的场景参考。",
        "suggestions": [
            {
                "suggestionKey": "rainy-yard",
                "displayName": "雨后小院",
                "purpose": "environment",
                "targetScope": "scene",
                "rationale": "建立环境",
                "prompt": "雨后小院空镜",
                "referenceAssetIds": [str(unavailable_planning_reference_id)],
            }
        ],
        "textOnlyItems": [],
    }
    accepted = AcceptedVisualAssetPlan(
        selections=[
            VisualAssetPlanSelection(
                suggestionKey="rainy-yard",
                action="generate",
                displayName="雨后小院",
                purpose="environment",
                targetScope="scene",
                prompt="雨后小院空镜",
                referenceAssetIds=[unavailable_planning_reference_id],
            )
        ]
    )
    step = StoredStep(
        id=uuid.uuid4(),
        project_id=repository.project.id,
        scene_id=repository.scene.id,
        shot_card_id=None,
        kind=StepKind.DIRECTOR,
        status=StepStatus.SUCCEEDED,
        attempt=1,
        operation_key="director:visual-asset-plan",
        input_snapshot={
            **repository.storyboard_production_context(repository.scene.id),
            "providerOutput": provider_output,
            "acceptedOutput": accepted.model_dump(mode="json", by_alias=True),
            "acceptedAt": datetime.now(UTC).isoformat(),
        },
    )
    repository.steps.append(step)
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,  # type: ignore[arg-type]
        director=gateway,
        provider_name="fake",
    )
    revised_plan = AcceptedVisualAssetPlan(
        selections=[
            accepted.selections[0].model_copy(update={"action": VisualAssetAction.SKIP})
        ]
    )

    revised = service.revise_visual_asset_plan(
        step.id,
        expected_revision=1,
        plan=revised_plan,
        note="已有环境参考足够，跳过重复生成",
    )

    assert revised.attempt == 2
    assert revised.provider == "manual"
    assert revised.input_snapshot["acceptedOutput"]["selections"][0]["action"] == "skip"
    assert repository.revision_call is not None
    assert gateway.calls == []

    with pytest.raises(RevisionConflictError, match="最新规划版本"):
        service.revise_visual_asset_plan(
            step.id,
            expected_revision=0,
            plan=revised_plan,
            note="过期页面提交",
        )


def test_story_rewrite_requires_accepted_diagnosis_and_updates_existing_source_text() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )
    diagnosis = service.diagnose_story(repository.scene.id, allow_paid_generation=True)

    with pytest.raises(ValueError, match="accepted story diagnosis"):
        service.rewrite_story(
            repository.scene.id,
            diagnosis_step_id=diagnosis.step_id,
            allow_paid_generation=True,
        )

    service.accept_story_diagnosis(
        diagnosis.step_id,
        diagnosis=diagnosis.output,
        selected_strategy="balanced",
        additional_instructions="",
        preserve_original=False,
    )
    gateway.payload = _rewrite_payload()
    rewrite = service.rewrite_story(
        repository.scene.id,
        diagnosis_step_id=diagnosis.step_id,
        allow_paid_generation=True,
    )
    accepted = rewrite.output.model_copy(
        update={"rewritten_story": "人工编辑后的完整连续剧情。"}
    )
    service.accept_story_rewrite(rewrite.step_id, rewrite=accepted)

    assert repository.scene.draft.source_text == "人工编辑后的完整连续剧情。"
    snapshot = repository.get_step(rewrite.step_id).input_snapshot
    assert snapshot["providerOutput"]["rewrittenStory"].startswith("孩子")
    assert snapshot["acceptedOutput"]["rewrittenStory"].startswith("人工编辑")


def test_story_rewrite_uses_only_the_latest_accepted_diagnosis_version() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )

    first = service.diagnose_story(repository.scene.id, allow_paid_generation=True)
    service.accept_story_diagnosis(
        first.step_id,
        diagnosis=first.output,
        selected_strategy="balanced",
        additional_instructions="第一版",
        preserve_original=False,
    )
    second = service.diagnose_story(repository.scene.id, allow_paid_generation=True)
    service.accept_story_diagnosis(
        second.step_id,
        diagnosis=second.output,
        selected_strategy="balanced",
        additional_instructions="第二版作为当前版本",
        preserve_original=False,
    )

    workflow = service.creative_workflow(repository.scene.id)
    assert [item["attempt"] for item in workflow["stages"]["diagnosis"]] == [2, 1]

    gateway.payload = _rewrite_payload()
    with pytest.raises(RevisionConflictError, match="latest accepted story diagnosis"):
        service.rewrite_story(
            repository.scene.id,
            diagnosis_step_id=first.step_id,
            allow_paid_generation=True,
        )

    rewrite = service.rewrite_story(
        repository.scene.id,
        diagnosis_step_id=second.step_id,
        allow_paid_generation=True,
    )
    assert rewrite.output.rewritten_story.startswith("孩子")


def test_preserve_original_skips_rewrite_but_allows_storyboard_dependency() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )
    diagnosis = service.diagnose_story(repository.scene.id, allow_paid_generation=True)
    service.accept_story_diagnosis(
        diagnosis.step_id,
        diagnosis=diagnosis.output,
        selected_strategy=None,
        additional_instructions="保留当前原稿",
        preserve_original=True,
    )

    with pytest.raises(ValueError, match="selected diagnosis rewrite strategy"):
        service.rewrite_story(
            repository.scene.id,
            diagnosis_step_id=diagnosis.step_id,
            allow_paid_generation=True,
        )
    assert service._approved_story_step(repository.scene).id == diagnosis.step_id


def test_accepting_stale_story_stage_is_rejected() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )
    diagnosis = service.diagnose_story(repository.scene.id, allow_paid_generation=True)
    repository.scene = replace(
        repository.scene,
        draft=repository.scene.draft.model_copy(update={"source_text": "用户已修改原稿。"}),
    )

    with pytest.raises(RevisionConflictError, match="changed"):
        service.accept_story_diagnosis(
            diagnosis.step_id,
            diagnosis=diagnosis.output,
            selected_strategy="balanced",
            additional_instructions="",
            preserve_original=False,
        )


def test_creative_workflow_keeps_original_story_and_names_current_rewrite_source() -> None:
    repository = _Repository()
    gateway = _Gateway()
    service = ProjectEditingService(
        repository=repository,
        director=gateway,
        provider_name="fake",
    )
    diagnosis = service.diagnose_story(repository.scene.id, allow_paid_generation=True)
    service.accept_story_diagnosis(
        diagnosis.step_id,
        diagnosis=diagnosis.output,
        selected_strategy="balanced",
        additional_instructions="",
        preserve_original=False,
    )
    gateway.payload = _rewrite_payload()
    rewrite = service.rewrite_story(
        repository.scene.id,
        diagnosis_step_id=diagnosis.step_id,
        allow_paid_generation=True,
    )
    service.accept_story_rewrite(
        rewrite.step_id,
        rewrite=rewrite.output.model_copy(
            update={"rewritten_story": "人工批准的新剧情。"}
        ),
    )

    workflow = service.creative_workflow(repository.scene.id)

    assert workflow["originalStory"] == "孩子和猫咪整理装备后出门。"
    assert workflow["currentStory"] == "人工批准的新剧情。"
    assert workflow["currentStorySource"] == "accepted_rewrite"
    assert workflow["currentStorySourceStepId"] == str(rewrite.step_id)


def test_visual_review_returns_editable_creative_body_and_stable_alternative() -> None:
    payload = {
        "actionDensityAssessment": "动作略密，需要减少重复拿取。",
        "assetCompatibilityAssessment": "定妆图表现的是动作完成状态，不适合作为开场全参考。",
        "pacingPlan": {
            "recommendedDurationSeconds": 10,
            "rationale": "保留建立、动作和稳定结果。",
            "beats": [
                {"ordinal": 1, "description": "建立起点", "rhythm": "brief"},
                {"ordinal": 2, "description": "完成动作", "rhythm": "expanded"},
            ],
        },
        "recommendedSceneLookUsage": "appearance_only",
        "recommendedAnchorMode": "generate",
        "referenceDecisions": [],
        "continuity": {
            "previousIssues": [],
            "nextIssues": [],
            "recommendation": "以道具放稳作为切点。",
        },
        "promptRisks": ["当前定妆姿态晚于片段动作起点"],
        "creativeBody": "1. 中景建立动作起点。\n2. 近景完成单一动作并稳定收尾。",
        "creativeAlternatives": [
            {
                "label": "stable",
                "body": "1. 固定中景建立起点。\n2. 固定近景完成动作并停稳。",
                "rationale": "减少运镜与状态跳变。",
            }
        ],
        "patch": {
            "direction": "1. 中景建立动作起点。\n2. 近景完成单一动作并稳定收尾。"
        },
    }

    analysis = ShotAssistAnalysis.model_validate(payload)

    assert analysis.creative_body.startswith("1. 中景")
    assert analysis.creative_alternatives[0].label == "stable"
    assert "动作完成状态" in analysis.asset_compatibility_assessment


def test_visual_review_prompt_requires_actual_image_suitability_review() -> None:
    current = ShotCardDraft(
        title="准备",
        direction="1. 中景，人物开始准备。\n2. 近景，猫咪观察并稳定收尾。",
        durationSeconds=10,
    )
    prompt = compile_shot_assistance_prompt(
        project_title="湖边日常",
        scene_title="出门准备",
        scene_text="人物和猫咪准备出门。",
        current=current,
        previous=None,
        following=None,
        visual_profile=VisualProfileDraft(),
        local_analysis=analyze_shot_draft(current),
        reference_manifest=(
            "@图片1=场景基础定妆；来源=scene；当前职责=scene",
        ),
    )

    assert "实际查看每张图片" in prompt
    assert "动作起始状态" in prompt
    assert "Seedance 创作正文" in prompt
    assert "保守版和稳定版" in prompt


def test_video_prompt_preview_parts_separate_creative_body_from_system_shell() -> None:
    context = ShotPromptContext(
        project_title="湖边日常",
        scene_title="出门准备",
        scene_text="人物和猫咪准备出门。",
        shot_title="整理装备",
        direction="1. 中景建立位置。\n2. 近景完成动作并稳定收尾。",
        duration_seconds=10,
    )
    parts = compile_shot_video_prompt_parts(
        context,
        build_shot_input_plan(
            resolution="480p",
            duration_seconds=10,
            anchor=None,
        ),
        binding_descriptions=(),
    )

    assert parts.creative_body == context.direction
    assert context.direction not in parts.system_shell.text
    assert "由片段已确认正文注入" in parts.system_shell.text
    assert context.direction in parts.final.text
    assert "480p" in parts.system_shell.text


def test_precompiled_video_prompt_is_not_wrapped_in_a_duplicate_reference_contract() -> None:
    creative_body = """任务：生成一个 9:16、8秒的原创二维治愈生活短片。

参考职责：
@图片1 是儿童当前唯一身份与本集造型来源。
@图片2 是猫咪当前唯一身份与本集造型来源。

身份连续性：儿童保持齐下颌短发；猫咪保持灰白虎斑和四足结构。

镜头正文：孩子在窗边发现纸星星，猫咪用鼻尖推回，最后一起贴到玻璃上。"""
    context = ShotPromptContext(
        project_title="纸星星",
        scene_title="清晨窗边",
        scene_text="孩子和猫咪在窗边迎接阳光。",
        shot_title="晨风吹落的星",
        direction=creative_body,
        duration_seconds=8,
    )
    plan = build_shot_input_plan(
        resolution="720p",
        duration_seconds=8,
        anchor=None,
        references=tuple(
            MediaSource(
                asset_id=uuid.UUID(int=index),
                semantic_key=f"reference:{index}",
                media_type="image",
                sha256=f"{index:064x}",
                metadata={},
            )
            for index in (1, 2)
        ),
    )

    parts = compile_shot_video_prompt_parts(
        context,
        plan,
        binding_descriptions=(
            "@图片1=本集儿童设计；职责=儿童身份与本集造型",
            "@图片2=本集猫咪设计；职责=猫咪身份、花纹与四足结构",
        ),
        precompiled_creative_body=True,
    )

    assert parts.creative_body == creative_body
    assert creative_body in parts.final.text
    assert creative_body not in parts.system_shell.text
    assert parts.final.text.count("参考职责：") == 1
    assert parts.final.text.count("身份连续性：") == 1
    assert "【主体、画风和素材职责】" not in parts.final.text
    assert "【片段内子镜头、动作路径和结果】" not in parts.final.text
    assert "【执行规格】" in parts.final.text
    assert "@图片1、@图片2" in parts.final.text
    assert "720p" in parts.final.text


def test_project_canon_repair_preserves_profile_text_and_cleans_misbound_scene_looks() -> None:
    repository = _Repository()
    service = ProjectEditingService(
        repository=repository,
        director=None,
        provider_name="fake",
    )

    result = service.restore_project_canon_references(repository.project.id)

    assert result["referenceCount"] == 3
    assert result["cleanedShotCount"] == 2
    assert repository.restored_profile is not None
    assert repository.restored_profile.person_identity == repository.profile.draft.person_identity
    assert {item.purpose.value for item in repository.restored_profile.reference_bindings} == {
        "person_identity",
        "cat_identity",
        "style",
    }
