"""HTTP boundary for the typed AIGC canvas.

V2 is additive: existing V1 endpoints remain available while projects are
enabled progressively.  The application service owns workflow decisions;
this module owns public validation, status codes and SSE framing.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date
from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, Header, Query, Response, status
from pydantic import Field, model_validator

from ..domain.aigc_canvas import (
    CanvasDiagnostic,
    ProductionFlowDto,
    ProjectWorkspaceShellDto,
    ScriptWorkspaceDto,
    StoryBrief,
    SubjectCompletionField,
    SubjectDraft,
    VideoWorkbenchDto,
    storyboard_quality_diagnostics,
)
from ..domain.contract_base import StrictModel
from ..domain.contracts import VisualProfileDraft
from ..domain.universal_canvas import VideoEditAnnotation, VideoEditRecipeDraft
from .http_headers import parse_version_header
from .jobs import JobConflictError, JobRegistry


class CanvasV2Service(Protocol):
    def create_child_cat_project(self, payload: CreateChildCatProjectRequest) -> dict[str, Any]: ...

    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]: ...

    def create_subject(self, project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]: ...

    def create_subject_revision(
        self, subject_id: uuid.UUID, payload: SubjectDraft
    ) -> dict[str, Any]: ...

    def create_subject_completion_run(
        self, project_id: uuid.UUID, payload: SubjectAssistantRunRequest
    ) -> dict[str, Any]: ...

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]: ...

    def apply_subject_completion(
        self, run_id: uuid.UUID, payload: SubjectAssistantApplyRequest
    ) -> dict[str, Any]: ...

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def list_visual_presets(self) -> list[dict[str, Any]]: ...

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, Any]: ...

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VisualProfileDraft,
    ) -> dict[str, Any]: ...

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]: ...

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]: ...

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def run_story_strategies(
        self, project_id: uuid.UUID, payload: StoryStrategyRunRequest
    ) -> dict[str, Any]: ...

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]: ...

    def edit_story_revision(self, revision_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, Any]: ...

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ShotBeatPatch,
    ) -> dict[str, Any]: ...

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ManualStoryboardDraftRequest,
    ) -> dict[str, Any]: ...

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: StoryboardPromptCompilationRequest,
    ) -> dict[str, Any]: ...

    def create_generation_attempt(self, payload: GenerationAttemptRequest) -> dict[str, Any]: ...

    def retry_generation_attempt(
        self, attempt_id: uuid.UUID, payload: RetryGenerationRequest
    ) -> dict[str, Any]: ...

    def review_asset(self, asset_id: uuid.UUID, payload: AssetReviewRequest) -> dict[str, Any]: ...

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...

    def get_workspace_shell(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def get_script_workspace(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def get_production_flow(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def save_production_flow_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: ProductionFlowLayoutPatch,
    ) -> dict[str, Any]: ...

    def get_video_workbench(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def replace_shot_beat_references(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ShotBeatReferenceBindingsRequest,
    ) -> dict[str, Any]: ...

    def get_asset_generation_lineage(self, asset_id: uuid.UUID) -> dict[str, Any]: ...

    def create_video_edit_recipe(self, payload: VideoEditRecipeDraft) -> dict[str, Any]: ...

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VideoEditRecipePatch,
    ) -> dict[str, Any]: ...

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VideoEditAnnotationsRequest,
    ) -> dict[str, Any]: ...

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, Any]: ...

    def submit_video_edit_recipe(
        self, recipe_id: uuid.UUID, payload: SubmitVideoEditRequest
    ) -> dict[str, Any]: ...


class StoryStrategyRunRequest(StrictModel):
    idempotency_key: str | None = Field(
        alias="idempotencyKey", default=None, min_length=8, max_length=96
    )
    rewrite_instruction: str | None = Field(
        alias="rewriteInstruction", default=None, max_length=4_000
    )


class StoryDocumentEditRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=200_000)
    summary: str | None = Field(default=None, min_length=1, max_length=4_000)
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class StoryboardRunRequest(StrictModel):
    source_story_revision_id: uuid.UUID = Field(alias="sourceStoryRevisionId")
    creation_mode: Literal["from_story", "from_characters"] = Field(
        alias="creationMode",
        default="from_story",
    )
    reference_asset_ids: list[uuid.UUID] = Field(
        alias="referenceAssetIds",
        default_factory=list,
        max_length=6,
    )
    instruction: str | None = Field(default=None, max_length=4_000)
    idempotency_key: str | None = Field(
        alias="idempotencyKey",
        default=None,
        min_length=8,
        max_length=96,
    )
    rewrite_instruction: str | None = Field(
        alias="rewriteInstruction", default=None, max_length=4_000
    )


class SubjectAssistantRunRequest(StrictModel):
    subject_id: uuid.UUID = Field(alias="subjectId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    instruction: str = Field(default="", max_length=4_000)


class SubjectAssistantApplyRequest(StrictModel):
    accepted_fields: list[SubjectCompletionField] = Field(
        alias="acceptedFields", min_length=1, max_length=5
    )
    final_draft: SubjectDraft = Field(alias="finalDraft")


class ShotBeatPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    direction: str | None = Field(default=None, min_length=1, max_length=6_000)
    action: str | None = Field(default=None, min_length=1, max_length=6_000)
    camera: str | None = Field(default=None, max_length=2_000)
    dialogue: str | None = Field(default=None, max_length=4_000)
    visual_description: str | None = Field(
        alias="visualDescription",
        default=None,
        max_length=6_000,
    )
    child_action: str | None = Field(alias="childAction", default=None, max_length=4_000)
    cat_action: str | None = Field(alias="catAction", default=None, max_length=4_000)
    spatial_relation: str | None = Field(alias="spatialRelation", default=None, max_length=4_000)
    contact_occlusion: str | None = Field(alias="contactOcclusion", default=None, max_length=4_000)
    shot_size: str | None = Field(alias="shotSize", default=None, max_length=200)
    lighting: str | None = Field(default=None, max_length=1_000)
    sound_effect: str | None = Field(alias="soundEffect", default=None, max_length=1_000)
    music_intent: str | None = Field(alias="musicIntent", default=None, max_length=1_000)
    wardrobe_state: str | None = Field(alias="wardrobeState", default=None, max_length=1_000)
    prop_state: str | None = Field(alias="propState", default=None, max_length=1_000)
    continuity_in: str | None = Field(alias="continuityIn", default=None, max_length=2_000)
    continuity_out: str | None = Field(alias="continuityOut", default=None, max_length=2_000)
    cut_intent: Literal["continuous", "soft_cut", "hard_cut"] | None = Field(
        alias="cutIntent", default=None
    )
    duration_seconds: int | None = Field(alias="durationSeconds", default=None, ge=1, le=60)
    subject_states: list[dict[str, Any]] | None = Field(
        alias="subjectStates", default=None, max_length=20
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_direction(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        direction = normalized.get("direction")
        if isinstance(direction, str) and direction.strip():
            normalized["action"] = direction
        elif isinstance(normalized.get("action"), str):
            normalized["direction"] = normalized["action"]
        return normalized


class ManualStoryboardShot(StrictModel):
    id: uuid.UUID | None = None
    scene_id: uuid.UUID | None = Field(alias="sceneId", default=None)
    revision: int | None = Field(default=None, ge=1)
    order: int = Field(ge=1, le=200)
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=60)
    title: str = Field(min_length=1, max_length=160)
    direction: str = Field(min_length=1, max_length=6_000)
    action: str = Field(min_length=1, max_length=6_000)
    visual_description: str = Field(alias="visualDescription", default="", max_length=6_000)
    child_action: str = Field(alias="childAction", default="", max_length=4_000)
    cat_action: str = Field(alias="catAction", default="", max_length=4_000)
    spatial_relation: str = Field(alias="spatialRelation", default="", max_length=4_000)
    contact_occlusion: str = Field(alias="contactOcclusion", default="", max_length=4_000)
    shot_size: str = Field(alias="shotSize", default="中景", max_length=200)
    lighting: str = Field(default="", max_length=500)
    dialogue: str = Field(default="", max_length=4_000)
    sound_effect: str = Field(alias="soundEffect", default="", max_length=1_000)
    music_intent: str = Field(alias="musicIntent", default="", max_length=1_000)
    wardrobe_state: str = Field(alias="wardrobeState", default="", max_length=1_000)
    prop_state: str = Field(alias="propState", default="", max_length=1_000)
    continuity_in: str = Field(alias="continuityIn", default="", max_length=2_000)
    continuity_out: str = Field(alias="continuityOut", default="", max_length=2_000)
    cut_intent: Literal["continuous", "soft_cut", "hard_cut"] = Field(
        alias="cutIntent", default="continuous"
    )
    camera: str = Field(default="", max_length=2_000)
    prompt: str = Field(default="", max_length=8_000)
    prompt_id: uuid.UUID | None = Field(alias="promptId", default=None)
    prompt_input_hash: str | None = Field(
        alias="promptInputHash",
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_direction(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        direction = normalized.get("direction")
        if isinstance(direction, str) and direction.strip():
            normalized["action"] = direction
        elif isinstance(normalized.get("action"), str):
            normalized["direction"] = normalized["action"]
        return normalized


class ManualStoryboardDraftRequest(StrictModel):
    shots: list[ManualStoryboardShot] = Field(min_length=1, max_length=200)
    healing_recipe: bool = Field(alias="healingRecipe", default=False)

    @model_validator(mode="after")
    def validate_shots(self) -> ManualStoryboardDraftRequest:
        orders = [shot.order for shot in self.shots]
        if sorted(orders) != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头顺序必须从1开始且连续")
        if self.healing_recipe and any(
            not 2 <= shot.duration_seconds <= 15 for shot in self.shots
        ):
            raise ValueError("治愈组合包每个导演分镜必须为2至15秒")
        return self

    @property
    def diagnostics(self) -> list[CanvasDiagnostic]:
        return storyboard_quality_diagnostics(self.shots)


class StoryboardPromptCompilationShot(StrictModel):
    beat_id: uuid.UUID | None = Field(alias="beatId", default=None)
    shot_card_id: uuid.UUID | None = Field(alias="shotCardId", default=None)
    editorial_shot_ids: list[uuid.UUID] = Field(
        alias="editorialShotIds", default_factory=list, max_length=20
    )
    expected_revision: int = Field(alias="expectedRevision", default=0, ge=0)
    order: int = Field(ge=1, le=200)
    scene_id: uuid.UUID = Field(alias="sceneId")
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=60)
    title: str = Field(min_length=1, max_length=160)
    direction: str = Field(min_length=1, max_length=6_000)
    action: str = Field(min_length=1, max_length=6_000)
    shot_size: str = Field(alias="shotSize", default="中景", max_length=200)
    lighting: str = Field(default="", max_length=500)
    dialogue: str = Field(default="", max_length=4_000)
    sound_effect: str = Field(alias="soundEffect", default="", max_length=1_000)
    camera: str = Field(default="", max_length=2_000)
    temporal_beats: list[dict[str, Any]] = Field(
        alias="temporalBeats",
        default_factory=list,
        max_length=20,
    )
    composition_asset_ids: list[uuid.UUID] = Field(
        alias="compositionAssetIds",
        default_factory=list,
        max_length=6,
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_direction(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        direction = normalized.get("direction")
        if isinstance(direction, str) and direction.strip():
            normalized["action"] = direction
        elif isinstance(normalized.get("action"), str):
            normalized["direction"] = normalized["action"]
        return normalized


class StoryboardPromptCompilationRequest(StrictModel):
    story_revision_id: uuid.UUID = Field(alias="storyRevisionId")
    storyboard_revision_id: uuid.UUID | None = Field(
        alias="storyboardRevisionId", default=None
    )
    structure_hash: str | None = Field(
        alias="structureHash", default=None, pattern=r"^[0-9a-f]{64}$"
    )
    generation_plan_id: uuid.UUID | None = Field(alias="generationPlanId", default=None)
    generation_plan_hash: str | None = Field(
        alias="generationPlanHash", default=None, pattern=r"^[0-9a-f]{64}$"
    )
    visual_profile_revision_id: uuid.UUID = Field(alias="visualProfileRevisionId")
    shots: list[StoryboardPromptCompilationShot] = Field(min_length=1, max_length=200)
    healing_recipe: bool = Field(alias="healingRecipe", default=False)

    @model_validator(mode="after")
    def validate_shots(self) -> StoryboardPromptCompilationRequest:
        orders = [shot.order for shot in self.shots]
        if sorted(orders) != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头顺序必须从1开始且连续")
        if self.healing_recipe and any(
            not 4 <= shot.duration_seconds <= 15 for shot in self.shots
        ):
            raise ValueError("治愈组合包每个真实生成片段必须为4至15秒")
        lineage_fields = (
            self.storyboard_revision_id,
            self.structure_hash,
            self.generation_plan_id,
            self.generation_plan_hash,
        )
        if any(item is not None for item in lineage_fields) and any(
            item is None for item in lineage_fields
        ):
            raise ValueError("新分镜 Prompt 编译必须同时固定分镜版本、结构哈希和生成编排")
        if self.healing_recipe and any(item is None for item in lineage_fields):
            raise ValueError("治愈组合包不能用临时镜头数组绕过分镜版本与生成编排")
        return self

    @property
    def diagnostics(self) -> list[CanvasDiagnostic]:
        return storyboard_quality_diagnostics(self.shots)


class GenerationAttemptRequest(StrictModel):
    project_id: uuid.UUID = Field(alias="projectId")
    business_object_type: str = Field(alias="businessObjectType", min_length=1, max_length=80)
    business_object_id: uuid.UUID = Field(alias="businessObjectId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    request: dict[str, Any]


class RetryGenerationRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    reason: str = Field(min_length=1, max_length=2_000)


class AssetReviewRequest(StrictModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=2_000)


class ProductionFlowLayoutPatch(StrictModel):
    nodes: list[dict[str, Any]] = Field(max_length=2_000)
    legacy_edges: list[dict[str, Any]] | None = Field(
        alias="edges",
        default=None,
        exclude=True,
        max_length=4_000,
    )
    viewport: dict[str, Any]
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)


class CreateChildCatBriefRequest(StrictModel):
    body: str = Field(min_length=1, max_length=20_000)
    duration_seconds: int = Field(alias="durationSeconds", ge=8, le=60)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = Field(alias="aspectRatio")
    quality_tier: Literal["quick", "balanced", "premium"] = Field(alias="qualityTier")


class CreateChildCatProjectRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    content_date: date | None = Field(alias="contentDate", default=None)
    brief: CreateChildCatBriefRequest
    child_canon_profile_id: str = Field(alias="childCanonProfileId", min_length=1, max_length=80)
    cat_canon_profile_id: str = Field(alias="catCanonProfileId", min_length=1, max_length=80)
    style_board_asset_id: uuid.UUID = Field(alias="styleBoardAssetId")


class ShotBeatReferenceBindingRequest(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    semantic_role: Literal[
        "composition",
        "pose",
        "wardrobe",
        "prop",
        "environment_detail",
    ] = Field(alias="semanticRole")
    instruction: str = Field(default="", max_length=2_000)
    ordinal: int = Field(ge=1, le=30)


class ShotBeatReferenceBindingsRequest(StrictModel):
    bindings: list[ShotBeatReferenceBindingRequest] = Field(default_factory=list, max_length=14)

    @model_validator(mode="after")
    def validate_order(self) -> ShotBeatReferenceBindingsRequest:
        ordinals = [item.ordinal for item in self.bindings]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError("镜头引用顺序必须从1开始且连续")
        asset_ids = [item.asset_id for item in self.bindings]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("同一素材不能在一个导演分镜中重复绑定")
        return self


class VideoEditRecipePatch(StrictModel):
    start_ms: int | None = Field(alias="startMs", default=None, ge=0)
    end_ms: int | None = Field(alias="endMs", default=None, gt=0)
    instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    reference_asset_ids: list[uuid.UUID] | None = Field(
        alias="referenceAssetIds", default=None, max_length=6
    )


class VideoEditAnnotationsRequest(StrictModel):
    annotations: list[VideoEditAnnotation] = Field(max_length=8)


class SubmitVideoEditRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    accept_estimated_cost_micros: int = Field(alias="acceptEstimatedCostMicros", ge=0)


def install_canvas_v2_routes(
    app: FastAPI,
    service: CanvasV2Service,
    jobs: JobRegistry,
) -> None:
    router = APIRouter(prefix="/api/v2")

    @router.get("/provider-capabilities")
    def list_provider_capabilities(
        media_kind: str | None = Query(
            alias="mediaKind", default=None, pattern="^(image|video|audio|video_edit)$"
        ),
    ) -> list[dict[str, Any]]:
        return service.list_provider_capabilities(media_kind=media_kind)

    @router.put("/projects/{project_id}/brief")
    def save_brief(project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        return service.save_brief(project_id, payload)

    @router.post("/projects/{project_id}/subjects", status_code=status.HTTP_201_CREATED)
    def create_subject(project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]:
        return service.create_subject(project_id, payload)

    @router.get("/projects/{project_id}/subjects")
    def list_subjects(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return service.list_subjects(project_id)

    @router.post("/subjects/{subject_id}/revisions", status_code=status.HTTP_201_CREATED)
    def create_subject_revision(
        subject_id: uuid.UUID,
        payload: SubjectDraft,
    ) -> dict[str, Any]:
        return service.create_subject_revision(subject_id, payload)

    @router.post(
        "/projects/{project_id}/subject-assistant-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_subject_completion_run(
        project_id: uuid.UUID,
        payload: SubjectAssistantRunRequest,
    ) -> dict[str, Any]:
        return service.create_subject_completion_run(project_id, payload)

    @router.get("/subject-assistant-runs/{run_id}")
    def get_subject_completion_run(run_id: uuid.UUID) -> dict[str, Any]:
        return service.get_subject_completion_run(run_id)

    @router.post(
        "/subject-assistant-runs/{run_id}/apply",
        status_code=status.HTTP_201_CREATED,
    )
    def apply_subject_completion(
        run_id: uuid.UUID,
        payload: SubjectAssistantApplyRequest,
    ) -> dict[str, Any]:
        return service.apply_subject_completion(run_id, payload)

    @router.get("/projects/{project_id}/assets")
    def list_project_assets(
        project_id: uuid.UUID,
        kind: str | None = Query(default=None, pattern="^(image|video|audio)$"),
    ) -> list[dict[str, Any]]:
        return service.list_project_assets(project_id, media_kind=kind)

    @router.get("/visual-presets")
    def list_visual_presets() -> list[dict[str, Any]]:
        return service.list_visual_presets()

    @router.post("/projects/{project_id}/visual-presets/{preset_key}/apply")
    def apply_visual_preset(
        project_id: uuid.UUID,
        preset_key: str,
    ) -> dict[str, Any]:
        return service.apply_visual_preset(project_id, preset_key)

    @router.get("/projects/{project_id}/visual-profile")
    def get_episode_visual_profile(project_id: uuid.UUID) -> dict[str, Any]:
        return service.get_episode_visual_profile(project_id)

    @router.patch("/projects/{project_id}/visual-profile")
    def update_episode_visual_profile(
        project_id: uuid.UUID,
        payload: VisualProfileDraft,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_episode_visual_profile(
            project_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post(
        "/assets/{asset_id}/filmstrip-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_video_filmstrip_run(
        asset_id: uuid.UUID,
        frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
    ) -> dict[str, Any]:
        return service.create_video_filmstrip_run(asset_id, frame_count=frame_count)

    @router.get("/assets/{asset_id}/filmstrip")
    def get_video_filmstrip(
        asset_id: uuid.UUID,
        frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
    ) -> dict[str, Any]:
        return service.get_video_filmstrip(asset_id, frame_count=frame_count)

    @router.post(
        "/projects/{project_id}/story-strategy-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story_strategies(
        project_id: uuid.UUID,
        payload: StoryStrategyRunRequest,
    ) -> dict[str, Any]:
        try:
            record = jobs.submit(
                kind="story_strategy",
                dedup_key=f"story_strategy:{project_id}:{payload.idempotency_key or 'default'}",
                fn=lambda: service.run_story_strategies(project_id, payload),
                context={
                    "projectId": project_id,
                    "canvasNodeId": uuid.uuid5(project_id, "story-planner"),
                    "operationKey": "canvas:story_strategy",
                    "workflowStage": "story",
                },
            )
        except JobConflictError as exc:
            return jobs.get(exc.job_id).to_dict()
        return record.to_dict()

    @router.post("/story-revisions/{revision_id}/approve")
    def approve_story_revision(
        revision_id: uuid.UUID,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        return service.approve_story_revision(revision_id)

    @router.post(
        "/story-revisions/{revision_id}/edits",
        status_code=status.HTTP_201_CREATED,
    )
    def edit_story_revision(
        revision_id: uuid.UUID,
        payload: StoryDocumentEditRequest,
    ) -> dict[str, Any]:
        return service.edit_story_revision(revision_id, payload)

    @router.post(
        "/projects/{project_id}/storyboard-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_storyboard(
        project_id: uuid.UUID,
        payload: StoryboardRunRequest,
    ) -> dict[str, Any]:
        if payload.creation_mode == "from_characters" and not payload.reference_asset_ids:
            raise ValueError("基于固定角色补充分镜至少需要一个已批准角色素材")
        create_parameters = inspect.signature(service.create_storyboard).parameters
        if "idempotency_key" not in create_parameters:
            # Compatibility boundary for older CanvasV2 service implementations:
            # validation still happens before a 202 response. The production service
            # exposes the extended signature and always runs inside JobRegistry.
            result = service.create_storyboard(project_id)

            def run() -> dict[str, Any]:
                return result
        else:

            def run() -> dict[str, Any]:
                return service.create_storyboard(
                    project_id,
                    source_story_revision_id=payload.source_story_revision_id,
                    idempotency_key=payload.idempotency_key,
                    creation_mode=payload.creation_mode,
                    reference_asset_ids=tuple(payload.reference_asset_ids),
                    instruction=payload.instruction,
                )

        try:
            record = jobs.submit(
                kind="storyboard",
                dedup_key=f"storyboard:{project_id}:{payload.idempotency_key or 'default'}",
                fn=run,
                context={
                    "projectId": project_id,
                    "canvasNodeId": uuid.uuid5(project_id, "storyboard-director"),
                    "creationMode": payload.creation_mode,
                    "operationKey": "canvas:storyboard",
                    "workflowStage": "storyboard",
                },
            )
        except JobConflictError as exc:
            return jobs.get(exc.job_id).to_dict()
        return record.to_dict()

    @router.patch("/shot-beats/{beat_id}")
    def update_shot_beat(
        beat_id: uuid.UUID,
        payload: ShotBeatPatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_shot_beat(
            beat_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.put("/shot-beats/{beat_id}/reference-bindings")
    def replace_shot_beat_references(
        beat_id: uuid.UUID,
        payload: ShotBeatReferenceBindingsRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.replace_shot_beat_references(
            beat_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.put("/projects/{project_id}/storyboard-drafts")
    def save_manual_storyboard(
        project_id: uuid.UUID,
        payload: ManualStoryboardDraftRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_manual_storyboard(
            project_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post("/projects/{project_id}/storyboard-prompt-compilations")
    def compile_storyboard_prompts(
        project_id: uuid.UUID,
        payload: StoryboardPromptCompilationRequest,
    ) -> dict[str, Any]:
        return service.compile_storyboard_prompts(project_id, payload)

    @router.post("/generation-attempts", status_code=status.HTTP_202_ACCEPTED)
    def create_generation_attempt(payload: GenerationAttemptRequest) -> dict[str, Any]:
        return service.create_generation_attempt(payload)

    @router.get("/assets/{asset_id}/generation-lineage")
    def get_asset_generation_lineage(asset_id: uuid.UUID) -> dict[str, Any]:
        return service.get_asset_generation_lineage(asset_id)

    @router.post("/video-edit-recipes", status_code=status.HTTP_201_CREATED)
    def create_video_edit_recipe(payload: VideoEditRecipeDraft) -> dict[str, Any]:
        return service.create_video_edit_recipe(payload)

    @router.patch("/video-edit-recipes/{recipe_id}", status_code=status.HTTP_201_CREATED)
    def update_video_edit_recipe(
        recipe_id: uuid.UUID,
        payload: VideoEditRecipePatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_video_edit_recipe(
            recipe_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.put("/video-edit-recipes/{recipe_id}/annotations")
    def replace_video_edit_annotations(
        recipe_id: uuid.UUID,
        payload: VideoEditAnnotationsRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.replace_video_edit_annotations(
            recipe_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post("/video-edit-recipes/{recipe_id}/compile")
    def compile_video_edit_recipe(recipe_id: uuid.UUID, _payload: dict[str, Any]) -> dict[str, Any]:
        return service.compile_video_edit_recipe(recipe_id)

    @router.post(
        "/video-edit-recipes/{recipe_id}/submit",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_video_edit_recipe(
        recipe_id: uuid.UUID, payload: SubmitVideoEditRequest
    ) -> dict[str, Any]:
        return service.submit_video_edit_recipe(recipe_id, payload)

    @router.post(
        "/generation-attempts/{attempt_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def retry_generation_attempt(
        attempt_id: uuid.UUID,
        payload: RetryGenerationRequest,
    ) -> dict[str, Any]:
        return service.retry_generation_attempt(attempt_id, payload)

    @router.post("/assets/{asset_id}/review")
    def review_asset(asset_id: uuid.UUID, payload: AssetReviewRequest) -> dict[str, Any]:
        return service.review_asset(asset_id, payload)

    @router.get("/prompt-runs/{prompt_id}")
    def get_prompt_run(prompt_id: uuid.UUID) -> dict[str, Any]:
        return service.get_prompt_run(prompt_id)

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_child_cat_project(payload: CreateChildCatProjectRequest) -> dict[str, Any]:
        return service.create_child_cat_project(payload)

    @router.get(
        "/projects/{project_id}/workspace-shell",
        response_model=ProjectWorkspaceShellDto,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    def get_workspace_shell(project_id: uuid.UUID) -> ProjectWorkspaceShellDto:
        return ProjectWorkspaceShellDto.model_validate(service.get_workspace_shell(project_id))

    @router.get(
        "/projects/{project_id}/script-workspace",
        response_model=ScriptWorkspaceDto,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    def get_script_workspace(project_id: uuid.UUID) -> ScriptWorkspaceDto:
        return ScriptWorkspaceDto.model_validate(service.get_script_workspace(project_id))

    @router.get(
        "/projects/{project_id}/production-flow",
        response_model=ProductionFlowDto,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    def get_production_flow(project_id: uuid.UUID) -> ProductionFlowDto:
        return ProductionFlowDto.model_validate(service.get_production_flow(project_id))

    @router.patch("/projects/{project_id}/production-flow/layout")
    def save_production_flow_layout(
        project_id: uuid.UUID,
        payload: ProductionFlowLayoutPatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_production_flow_layout(
            project_id,
            expected_version=parse_version_header(if_match),
            payload=payload,
        )

    @router.get(
        "/projects/{project_id}/video-workbench",
        response_model=VideoWorkbenchDto,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    def get_video_workbench(project_id: uuid.UUID) -> VideoWorkbenchDto:
        return VideoWorkbenchDto.model_validate(service.get_video_workbench(project_id))

    @router.options("/{path:path}", include_in_schema=False)
    def v2_options(_path: str) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
