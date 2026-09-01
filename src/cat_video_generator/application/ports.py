"""Boundaries used by the V5 creation-flow application services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from ..domain.contracts import (
    AcceptedVisualAssetPlan,
    ReferenceBinding,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    ShotAssistPatch,
    ShotCardDraft,
    StoryProjectInput,
    VisualProfileDraft,
)
from ..domain.rendering import ProjectSequencePlan, SequenceStatus, VideoInputPlan
from ..domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
)


def reference_display_name(
    *,
    semantic_key: str | None,
    role: str,
    metadata: dict[str, Any],
) -> str:
    """Return the stable creator-facing name for a reference asset.

    Generated assets historically stored their internal semantic key in
    ``displayName``.  That key is useful for lineage, but it must never leak
    into a creator-facing Prompt or reference strip.  Keep the semantic key in
    audit data and derive the visible production role here.
    """

    semantic = semantic_key or ""
    character_design = metadata.get("characterDesign")
    slot = (
        str(character_design.get("slot") or "")
        if isinstance(character_design, dict)
        else ""
    )
    if not slot and semantic.startswith("character-design:"):
        parts = semantic.split(":")
        slot = parts[2] if len(parts) > 2 else ""
    slot_names = {
        "child": "本集儿童设计",
        "cat": "本集猫咪设计",
        "pair_scale": "一人一猫同框比例",
    }
    if slot in slot_names:
        return slot_names[slot]

    shot_parts = semantic.split(":")
    if len(shot_parts) >= 3 and shot_parts[0] == "shot":
        if shot_parts[2] == "video" and len(shot_parts) >= 4:
            return f"视频版本 V{shot_parts[3]}"
        if shot_parts[2] == "anchor":
            return "开场视觉锚点"
        if shot_parts[2] == "tail":
            return "视频真实尾帧"

    configured = metadata.get("displayName") or metadata.get("title")
    if isinstance(configured, str) and configured.strip():
        value = configured.strip()
        if not value.startswith("character-design:"):
            return value
    names = {
        "person:headshot": "人物大头照",
        "person:fullbody": "人物全身",
        "person:front": "人物正面",
        "person:side": "人物侧面",
        "person:back": "人物背面",
        "cat:front": "猫咪正面",
        "cat:side": "猫咪侧面",
        "cat:back": "猫咪背面",
        "style:line_texture": "线条与材质",
        "style:outdoor": "户外画风",
        "style:indoor": "室内画风",
    }
    return names.get(semantic, semantic or role)


@dataclass(frozen=True, slots=True)
class DirectorResult:
    payload: dict[str, Any]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class CreativeDirectorResult:
    payload: dict[str, Any] | str
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    url: str
    model: str


@dataclass(frozen=True, slots=True)
class VideoTaskResult:
    task_id: str
    status: str
    video_url: str | None = None
    last_frame_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    model: str | None = None
    created_at: datetime | None = None
    duration_seconds: int | None = None
    ratio: str | None = None
    resolution: str | None = None
    generate_audio: bool | None = None


@dataclass(frozen=True, slots=True)
class VideoDiagnosticResult:
    identity_ok: bool
    identity_assessment: str
    style_ok: bool
    constraints_ok: bool
    narrative_order_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[dict[str, str | None], ...]
    shot_boundaries_seconds: tuple[float, ...]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageDiagnosticResult:
    identity_ok: bool
    style_ok: bool
    constraints_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[dict[str, str | None], ...]
    response_id: str
    model: str
    request_hash: str


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
        request_id: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        self.request_id = request_id
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class StoredProject:
    id: uuid.UUID
    title: str
    content_date: date
    status: RunStatus
    selected_sequence_id: uuid.UUID | None = None
    default_reference_bindings: tuple[ReferenceBinding, ...] = ()
    visual_profile_revision_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class StoredVisualProfileRevision:
    id: uuid.UUID
    project_id: uuid.UUID
    revision: int
    profile_hash: str
    source_profile_id: str
    draft: VisualProfileDraft
    reference_snapshot: tuple[dict[str, Any], ...] = ()
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredScene:
    id: uuid.UUID
    project_id: uuid.UUID
    order: int
    draft: SceneDraft
    status: SceneStatus
    selected_look_asset_id: uuid.UUID | None = None
    look_draft: SceneLookDraft | None = None
    look_draft_revision: int = 0


@dataclass(frozen=True, slots=True)
class StoredShot:
    id: uuid.UUID
    scene_id: uuid.UUID
    project_id: uuid.UUID
    order: int
    draft: ShotCardDraft
    status: ShotStatus
    draft_revision: int = 1
    selected_anchor_asset_id: uuid.UUID | None = None
    selected_video_asset_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class StoredStep:
    id: uuid.UUID
    project_id: uuid.UUID
    scene_id: uuid.UUID | None
    shot_card_id: uuid.UUID | None
    kind: StepKind
    status: StepStatus
    attempt: int
    operation_key: str
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    provider_task_id: str | None = None
    model: str | None = None
    error: dict[str, Any] | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    id: uuid.UUID
    step_id: uuid.UUID
    purpose: PromptPurpose
    model: str
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredAsset:
    id: uuid.UUID
    project_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    shot_card_id: uuid.UUID | None
    step_id: uuid.UUID | None
    role: str
    media_type: str
    scope: str
    status: str
    path: Path | None
    sha256: str
    metadata: dict[str, Any]
    semantic_key: str | None = None
    created_at: datetime | None = None

    @property
    def content_ready(self) -> bool:
        return self.path is not None and self.path.is_file()

    def require_path(self) -> Path:
        if not self.content_ready or self.path is None:
            raise ValueError(f"asset {self.id} content is unavailable")
        return self.path

    @property
    def display_name(self) -> str:
        return reference_display_name(
            semantic_key=self.semantic_key,
            role=self.role,
            metadata=self.metadata,
        )

    @property
    def reference_purpose(self) -> str | None:
        semantic_key = self.semantic_key or ""
        if semantic_key == "person:headshot":
            return "person_identity"
        if semantic_key.startswith("person:"):
            return "person_body"
        if semantic_key.startswith("cat:"):
            return "cat_identity"
        if semantic_key.startswith("style:"):
            return "style"
        value = self.metadata.get("referencePurpose")
        return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class StoredReview:
    id: uuid.UUID
    step_id: uuid.UUID
    asset_id: uuid.UUID | None
    source: str
    decision: str
    reason: str | None
    warnings: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredSequence:
    id: uuid.UUID
    project_id: uuid.UUID
    revision: int
    parent_sequence_id: uuid.UUID | None
    rendered_asset_id: uuid.UUID | None
    status: SequenceStatus
    plan: ProjectSequencePlan
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectReadModel:
    """Batch-loaded project state for read-heavy production projections."""

    project: StoredProject
    visual_profile: StoredVisualProfileRevision
    scenes: tuple[StoredScene, ...]
    shots: tuple[StoredShot, ...]
    steps: tuple[StoredStep, ...]
    prompts: tuple[StoredPrompt, ...]
    assets: tuple[StoredAsset, ...]
    reviews: tuple[StoredReview, ...]
    sequences: tuple[StoredSequence, ...]


@dataclass(frozen=True, slots=True)
class ShotGenerationReadModel:
    """Focused state for one generation workspace and its previous-shot continuity."""

    project: StoredProject
    visual_profile: StoredVisualProfileRevision
    scene: StoredScene
    shot: StoredShot
    scene_shots: tuple[StoredShot, ...]
    steps: tuple[StoredStep, ...]
    prompts: tuple[StoredPrompt, ...]
    assets: tuple[StoredAsset, ...]
    reviews: tuple[StoredReview, ...]


@dataclass(frozen=True, slots=True)
class LandedAsset:
    path: Path
    sha256: str
    byte_size: int


class DirectorGateway(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def analysis_model(self) -> str: ...

    def generate_creative_text(
        self,
        *,
        prompt: str,
        output_name: str,
    ) -> CreativeDirectorResult: ...

    def generate_storyboard_text(
        self,
        *,
        prompt: str,
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> CreativeDirectorResult: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> DirectorResult: ...

    def analyze_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...],
    ) -> DirectorResult: ...


class MediaGateway(Protocol):
    @property
    def image_model(self) -> str: ...

    @property
    def video_model(self) -> str: ...

    @property
    def review_model(self) -> str: ...

    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult: ...

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult: ...

    def get_video_task(self, task_id: str) -> VideoTaskResult: ...

    def cancel_video_task(self, task_id: str) -> VideoTaskResult: ...

    def list_video_tasks(
        self, *, model: str, page_size: int = 100
    ) -> tuple[VideoTaskResult, ...]: ...

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
        reference_paths: tuple[Path, ...] = (),
        reference_labels: tuple[str, ...] = (),
    ) -> VideoDiagnosticResult: ...

    def diagnose_image(self, *, prompt: str, image_path: Path) -> ImageDiagnosticResult: ...


class RuntimePreflight(Protocol):
    @property
    def video_resolution(self) -> str: ...

    @property
    def semantic_review_enabled(self) -> bool: ...

    def validate_for_video_generation(self, *, allow_paid_generation: bool) -> None: ...

    def validate_for_range_edit(self, *, allow_paid_generation: bool) -> None: ...

    def validate_for_local_composition(self) -> None: ...


class AssetStore(Protocol):
    def download(self, url: str, *, suffix: str) -> LandedAsset: ...

    def import_local(self, path: Path) -> LandedAsset: ...

    def compose_sequence(
        self,
        paths: tuple[Path, ...],
        plan: ProjectSequencePlan,
    ) -> LandedAsset: ...

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset: ...


class MediaProbe(Protocol):
    def inspect_image(self, path: Path) -> dict[str, Any]: ...

    def inspect_video(
        self,
        path: Path,
        *,
        expected_duration_seconds: int,
        expected_resolution: str,
        minimum_duration_seconds: int = 4,
        maximum_duration_seconds: int = 15,
        duration_tolerance_ms: int = 1000,
        require_audio: bool = True,
    ) -> dict[str, Any]: ...


class FrameExtractor(Protocol):
    def extract_review_frames(self, source: StoredAsset, *, count: int) -> tuple[Path, ...]: ...

    def extract_frames_at(
        self, source: StoredAsset, *, timestamps_ms: tuple[int, ...]
    ) -> tuple[Path, ...]: ...

    def extract_tail_frame(self, source: StoredAsset) -> tuple[Path, int]: ...


class ShotQueueStore(Protocol):
    def create_project(self, source: StoryProjectInput, *, content_date: date) -> StoredProject: ...

    def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        content_date: date,
    ) -> StoredProject: ...

    def list_projects(self) -> tuple[StoredProject, ...]: ...

    def get_project(self, project_id: uuid.UUID) -> StoredProject: ...

    def project_read_model(self, project_id: uuid.UUID) -> ProjectReadModel: ...

    def shot_generation_read_model(
        self,
        shot_id: uuid.UUID,
    ) -> ShotGenerationReadModel: ...

    def update_project_default_references(
        self,
        project_id: uuid.UUID,
        bindings: list[ReferenceBinding],
    ) -> StoredProject: ...

    def get_visual_profile(self, project_id: uuid.UUID) -> StoredVisualProfileRevision: ...

    def get_default_visual_profile(self, project_id: uuid.UUID) -> VisualProfileDraft: ...

    def get_visual_profile_revision(
        self,
        revision_id: uuid.UUID,
    ) -> StoredVisualProfileRevision: ...

    def save_visual_profile(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> StoredVisualProfileRevision: ...

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> tuple[StoredVisualProfileRevision, int]: ...

    def add_scene(self, project_id: uuid.UUID, draft: SceneDraft) -> StoredScene: ...

    def update_scene(self, scene_id: uuid.UUID, draft: SceneDraft) -> StoredScene: ...

    def delete_scene(self, scene_id: uuid.UUID) -> None: ...

    def reorder_scenes(self, project_id: uuid.UUID, scene_ids: tuple[uuid.UUID, ...]) -> None: ...

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]: ...

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene: ...

    def approved_character_design_assets(
        self, project_id: uuid.UUID
    ) -> tuple[StoredAsset, ...]: ...

    def requires_character_design_assets(self, project_id: uuid.UUID) -> bool: ...

    def storyboard_production_context(self, scene_id: uuid.UUID) -> dict[str, Any]: ...

    def generation_clip_production_context(
        self,
        shot_id: uuid.UUID,
    ) -> dict[str, Any]: ...

    def select_scene_look_asset(
        self,
        scene_id: uuid.UUID,
        asset_id: uuid.UUID | None,
    ) -> StoredScene: ...

    def get_scene_look_draft(self, scene_id: uuid.UUID) -> StoredScene: ...

    def save_scene_look_draft(
        self,
        scene_id: uuid.UUID,
        *,
        expected_revision: int,
        draft: SceneLookDraft,
    ) -> StoredScene: ...

    def add_shot(self, scene_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot: ...

    def replace_shots(
        self, scene_id: uuid.UUID, drafts: tuple[ShotCardDraft, ...]
    ) -> tuple[StoredShot, ...]: ...

    def accept_scene_suggestions(
        self,
        *,
        step_id: uuid.UUID,
        drafts: tuple[ShotCardDraft, ...],
        look_plan: SceneLookPlan | None,
        accepted_output: dict[str, Any],
        apply_mode: str,
        source_shot_revisions: dict[uuid.UUID, int],
    ) -> tuple[StoredShot, ...]: ...

    def accept_story_diagnosis(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
    ) -> StoredStep: ...

    def accept_story_rewrite(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
        rewritten_story: str,
    ) -> StoredScene: ...

    def accept_story_expansion(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
        expanded_story: str,
    ) -> StoredScene: ...

    def accept_visual_asset_plan(
        self,
        *,
        step_id: uuid.UUID,
        expected_storyboard_revision_id: uuid.UUID,
        expected_structure_hash: str,
        expected_generation_plan_id: uuid.UUID,
        expected_generation_plan_hash: str,
        accepted_output: AcceptedVisualAssetPlan,
    ) -> StoredStep: ...

    def revise_visual_asset_plan(
        self,
        *,
        step_id: uuid.UUID,
        expected_revision: int,
        accepted_output: AcceptedVisualAssetPlan,
        note: str,
    ) -> StoredStep: ...

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot: ...

    def accept_shot_assistance(
        self,
        *,
        step_id: uuid.UUID,
        source_draft_revision: int,
        patch: ShotAssistPatch | None,
        accepted_anchor_brief: str | None,
    ) -> StoredShot: ...

    def save_manual_anchor_brief(
        self,
        *,
        shot_id: uuid.UUID,
        source_draft_revision: int,
        brief: str,
        input_hash: str,
    ) -> StoredStep: ...

    def delete_shot(self, shot_id: uuid.UUID) -> None: ...

    def reorder_shots(self, scene_id: uuid.UUID, shot_ids: tuple[uuid.UUID, ...]) -> None: ...

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]: ...

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot: ...

    def next_attempt(self, *, shot_id: uuid.UUID, operation_key: str) -> int: ...

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int: ...

    def next_project_attempt(self, *, project_id: uuid.UUID, operation_key: str) -> int: ...

    def create_step_with_prompt(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        kind: StepKind,
        operation_key: str,
        attempt: int,
        provider: str,
        model: str,
        input_hash: str,
        input_snapshot: dict[str, Any],
        purpose: PromptPurpose,
        prompt_text: str,
    ) -> tuple[StoredStep, StoredPrompt]: ...

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, Any] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> StoredStep: ...

    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]: ...

    def task_center_steps(self, *, limit: int = 300) -> tuple[StoredStep, ...]: ...

    def get_prompt(self, step_id: uuid.UUID) -> StoredPrompt | None: ...

    def add_asset(
        self,
        *,
        landed: LandedAsset,
        role: str,
        media_type: str,
        scope: str,
        status: str,
        project_id: uuid.UUID | None,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        step_id: uuid.UUID | None,
        semantic_key: str | None,
        metadata: dict[str, Any],
    ) -> StoredAsset: ...

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset: ...

    def list_assets(
        self,
        *,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        include_canon: bool = False,
    ) -> tuple[StoredAsset, ...]: ...

    def select_shot_asset(
        self, shot_id: uuid.UUID, *, kind: str, asset_id: uuid.UUID
    ) -> StoredShot: ...

    def add_review(
        self,
        *,
        step_id: uuid.UUID,
        asset_id: uuid.UUID | None,
        source: str,
        decision: str,
        reason: str | None,
        warnings: tuple[dict[str, Any], ...],
        evidence: dict[str, Any],
    ) -> StoredReview: ...

    def decide_asset(
        self,
        asset_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
    ) -> StoredAsset: ...

    def list_reviews(self, step_id: uuid.UUID) -> tuple[StoredReview, ...]: ...

    def create_sequence(
        self,
        *,
        project_id: uuid.UUID,
        plan: ProjectSequencePlan,
        parent_sequence_id: uuid.UUID | None,
        rendered_asset_id: uuid.UUID | None,
        status: SequenceStatus,
    ) -> StoredSequence: ...

    def list_sequences(self, project_id: uuid.UUID) -> tuple[StoredSequence, ...]: ...

    def select_sequence(self, project_id: uuid.UUID, sequence_id: uuid.UUID) -> StoredSequence: ...

    def decide_sequence(self, sequence_id: uuid.UUID, *, approved: bool) -> StoredSequence: ...
