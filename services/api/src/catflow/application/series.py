from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from catflow.domain.contract import ContractModel
from catflow.domain.models import LifeStoryProposalDraft

SeriesNarrativeMode = Literal["continuous", "lightly_serialized", "anthology"]
SeriesLengthMode = Literal["fixed", "ongoing"]
SeriesPlanStatus = Literal["candidate", "accepted", "rejected", "superseded"]
SeriesPlanDisposition = Literal["candidate_ready", "needs_input", "invalid"]
SourceCoverageMode = Literal["whole", "partial", "continuation"]
MAX_SERIES_PLANNING_BATCH = 30
DEFAULT_ONGOING_PLANNING_BATCH = 12


class SeriesCreateCommand(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    premise: str = Field(min_length=1, max_length=4_000)
    narrative_mode: SeriesNarrativeMode = Field(alias="narrativeMode")
    length_mode: SeriesLengthMode = Field(alias="lengthMode", default="fixed")
    planned_episode_count: int | None = Field(alias="plannedEpisodeCount", default=None)
    default_episode_duration_seconds: int = Field(
        alias="defaultEpisodeDurationSeconds", ge=8, le=15
    )
    world_setting: str = Field(alias="worldSetting", min_length=1, max_length=2_000)
    emotional_direction: str = Field(alias="emotionalDirection", min_length=1, max_length=1_000)
    ending_goal: str | None = Field(alias="endingGoal", default=None, max_length=1_000)
    recurring_elements: list[str] = Field(
        alias="recurringElements", default_factory=list, max_length=30
    )
    must_keep: list[str] = Field(alias="mustKeep", default_factory=list, max_length=30)
    must_avoid: list[str] = Field(alias="mustAvoid", default_factory=list, max_length=30)
    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_series_length(self) -> SeriesCreateCommand:
        if self.length_mode == "fixed":
            if self.planned_episode_count is None or self.planned_episode_count < 2:
                raise ValueError("fixed series requires plannedEpisodeCount of at least 2")
        elif self.planned_episode_count is not None:
            raise ValueError("ongoing series cannot have plannedEpisodeCount")
        return self


class SeriesPatchCommand(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    premise: str | None = Field(default=None, min_length=1, max_length=4_000)
    world_setting: str | None = Field(alias="worldSetting", default=None, max_length=2_000)
    emotional_direction: str | None = Field(
        alias="emotionalDirection", default=None, max_length=1_000
    )
    ending_goal: str | None = Field(alias="endingGoal", default=None, max_length=1_000)
    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)

    @model_validator(mode="after")
    def require_change(self) -> SeriesPatchCommand:
        if not self.model_fields_set:
            raise ValueError("at least one series field is required")
        return self


class StorySeriesDto(SeriesCreateCommand):
    id: uuid.UUID
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    active_plan_version_id: uuid.UUID | None = Field(alias="activePlanVersionId", default=None)
    planned_count: int = Field(alias="plannedCount", default=0)
    materialized_count: int = Field(alias="materializedCount", default=0)
    completed_count: int = Field(alias="completedCount", default=0)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class RecurringLocationDraft(ContractModel):
    key: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=800)


class RecurringPropDraft(ContractModel):
    key: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=160)
    continuity_rule: str = Field(alias="continuityRule", default="", max_length=800)


class SeriesEmotionalArcDraft(ContractModel):
    opening: str = Field(default="", max_length=600)
    development: str = Field(default="", max_length=600)
    climax: str = Field(default="", max_length=600)
    resolution: str = Field(default="", max_length=600)


class SeriesBibleDraft(ContractModel):
    logline: str = Field(default="", max_length=800)
    central_theme: str = Field(alias="centralTheme", default="", max_length=300)
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    world_rules: list[str] = Field(alias="worldRules", default_factory=list)
    emotional_arc: SeriesEmotionalArcDraft = Field(
        alias="emotionalArc", default_factory=SeriesEmotionalArcDraft
    )
    recurring_locations: list[RecurringLocationDraft] = Field(
        alias="recurringLocations", default_factory=list
    )
    recurring_props: list[RecurringPropDraft] = Field(alias="recurringProps", default_factory=list)
    wardrobe_rules: list[str] = Field(alias="wardrobeRules", default_factory=list)
    continuity_rules: list[str] = Field(alias="continuityRules", default_factory=list)
    visual_motifs: list[str] = Field(alias="visualMotifs", default_factory=list)
    sound_motifs: list[str] = Field(alias="soundMotifs", default_factory=list)
    forbidden_changes: list[str] = Field(alias="forbiddenChanges", default_factory=list)


class EpisodeSourceCoverageDto(ContractModel):
    source_unit_ordinal: int = Field(alias="sourceUnitOrdinal", ge=1)
    coverage: SourceCoverageMode
    coverage_note: str = Field(alias="coverageNote", min_length=1, max_length=1_000)


class SeriesEpisodeOutlineDraft(ContractModel):
    order: int = Field(default=0, ge=0)
    title: str = Field(default="", max_length=160)
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds", default=0, ge=0, le=15
    )
    premise: str = Field(default="", max_length=1_000)
    opening_state: str = Field(alias="openingState", default="", max_length=1_000)
    trigger: str = Field(default="", max_length=800)
    child_intent: str = Field(alias="childIntent", default="", max_length=800)
    child_action: str = Field(alias="childAction", default="", max_length=1_200)
    cat_response: str = Field(alias="catResponse", default="", max_length=1_200)
    visible_change: str = Field(alias="visibleChange", default="", max_length=1_000)
    ending_state: str = Field(alias="endingState", default="", max_length=1_000)
    continuity_carryover: list[str] = Field(alias="continuityCarryover", default_factory=list)
    recurring_location_keys: list[str] = Field(alias="recurringLocationKeys", default_factory=list)
    recurring_prop_keys: list[str] = Field(alias="recurringPropKeys", default_factory=list)
    production_warnings: list[str] = Field(alias="productionWarnings", default_factory=list)
    source_coverage: list[EpisodeSourceCoverageDto] = Field(
        alias="sourceCoverage", default_factory=list
    )


class SeriesPlanDraft(ContractModel):
    series_bible: SeriesBibleDraft = Field(alias="seriesBible")
    episodes: list[SeriesEpisodeOutlineDraft] = Field(min_length=1)


class SeriesValidationIssueDto(ContractModel):
    code: str
    severity: Literal["fatal", "blocking", "warning"]
    path: str
    message: str
    suggested_action: str | None = Field(alias="suggestedAction", default=None)


@dataclass(frozen=True, slots=True)
class SeriesPlanNormalizationResult:
    raw_payload: dict[str, object]
    normalized_payload: dict[str, object] | None
    disposition: SeriesPlanDisposition
    issues: tuple[SeriesValidationIssueDto, ...]
    plan: SeriesPlanDraft | None = None

    @property
    def recoverable(self) -> bool:
        return self.plan is not None and self.disposition != "invalid"

    def validation_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "disposition": self.disposition,
            "recoverable": self.recoverable,
            "issues": [issue.model_dump(mode="json", by_alias=True) for issue in self.issues],
        }
        if self.normalized_payload is not None:
            document["normalizedPayload"] = self.normalized_payload
        return document


class SeriesPlanVersionDto(ContractModel):
    id: uuid.UUID
    series_id: uuid.UUID = Field(alias="seriesId")
    revision: int
    status: SeriesPlanStatus
    active: bool
    disposition: SeriesPlanDisposition
    plan: SeriesPlanDraft
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt_revision: str = Field(alias="promptRevision")
    producing_job_id: uuid.UUID | None = Field(alias="producingJobId", default=None)
    base_plan_version_id: uuid.UUID | None = Field(alias="basePlanVersionId", default=None)
    issues: list[SeriesValidationIssueDto] = Field(default_factory=list)
    decided_at: datetime | None = Field(alias="decidedAt", default=None)
    created_at: datetime = Field(alias="createdAt")


class SeriesEpisodeDto(ContractModel):
    id: uuid.UUID
    series_id: uuid.UUID = Field(alias="seriesId")
    order: int
    title: str
    target_duration_seconds: int = Field(alias="targetDurationSeconds")
    status: Literal[
        "outline",
        "story_review",
        "assets",
        "storyboard",
        "generating",
        "selecting",
        "editing",
        "completed",
        "needs_attention",
    ]
    project_id: uuid.UUID | None = Field(alias="projectId", default=None)
    active_outline_version_id: uuid.UUID = Field(alias="activeOutlineVersionId")
    outline: SeriesEpisodeOutlineDraft
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProjectSeriesContextDto(ContractModel):
    series: StorySeriesDto
    episode: SeriesEpisodeDto
    episodes: list[SeriesEpisodeDto]


class SeriesSourceBeatDto(ContractModel):
    id: uuid.UUID
    series_id: uuid.UUID = Field(alias="seriesId")
    source_unit_id: uuid.UUID = Field(alias="sourceUnitId")
    source_unit_ordinal: int = Field(alias="sourceUnitOrdinal")
    binding_order: int = Field(alias="bindingOrder")
    title: str
    theme: str | None = None
    raw_text: str = Field(alias="rawText")
    created_at: datetime = Field(alias="createdAt")


class SeriesPlanPreviewDto(ContractModel):
    series_id: uuid.UUID = Field(alias="seriesId")
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    planned_episode_count: int = Field(alias="plannedEpisodeCount")
    total_planned_episode_count: int | None = Field(
        alias="totalPlannedEpisodeCount", default=None
    )
    remaining_episode_count: int | None = Field(alias="remainingEpisodeCount", default=None)
    length_mode: SeriesLengthMode = Field(alias="lengthMode")
    default_episode_duration_seconds: int = Field(alias="defaultEpisodeDurationSeconds")
    prompt_revision: str = Field(alias="promptRevision")


class SeriesPlanGenerationCommand(ContractModel):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanSegmentCommand(ContractModel):
    start_episode_order: int = Field(alias="startEpisodeOrder", ge=1)
    requested_episode_count: int = Field(
        alias="requestedEpisodeCount", ge=1, le=MAX_SERIES_PLANNING_BATCH
    )
    expected_series_plan_version_id: uuid.UUID = Field(
        alias="expectedSeriesPlanVersionId"
    )
    expected_previous_segment_version_id: uuid.UUID | None = Field(
        alias="expectedPreviousSegmentVersionId", default=None
    )


class SeriesPlanSegmentGenerationCommand(SeriesPlanSegmentCommand):
    expected_input_hash: str = Field(
        alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$"
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanSegmentPreviewDto(ContractModel):
    series_id: uuid.UUID = Field(alias="seriesId")
    start_episode_order: int = Field(alias="startEpisodeOrder")
    requested_episode_count: int = Field(alias="requestedEpisodeCount")
    remaining_episode_count: int | None = Field(alias="remainingEpisodeCount", default=None)
    expected_series_plan_version_id: uuid.UUID = Field(
        alias="expectedSeriesPlanVersionId"
    )
    expected_previous_segment_version_id: uuid.UUID | None = Field(
        alias="expectedPreviousSegmentVersionId", default=None
    )
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    prompt_revision: str = Field(alias="promptRevision")


class SeriesPlanSegmentVersionDto(ContractModel):
    id: uuid.UUID
    segment_id: uuid.UUID = Field(alias="segmentId")
    series_id: uuid.UUID = Field(alias="seriesId")
    start_episode_order: int = Field(alias="startEpisodeOrder")
    requested_episode_count: int = Field(alias="requestedEpisodeCount")
    revision: int
    status: SeriesPlanStatus
    active: bool
    disposition: SeriesPlanDisposition
    plan: SeriesPlanDraft
    issues: list[SeriesValidationIssueDto] = Field(default_factory=list)
    producing_job_id: uuid.UUID | None = Field(alias="producingJobId", default=None)
    expected_series_plan_version_id: uuid.UUID = Field(
        alias="expectedSeriesPlanVersionId"
    )
    previous_segment_version_id: uuid.UUID | None = Field(
        alias="previousSegmentVersionId", default=None
    )
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt_revision: str = Field(alias="promptRevision")
    decided_at: datetime | None = Field(alias="decidedAt", default=None)
    created_at: datetime = Field(alias="createdAt")


class SeriesPlanSegmentActivationCommand(ContractModel):
    expected_series_plan_version_id: uuid.UUID = Field(
        alias="expectedSeriesPlanVersionId"
    )
    expected_previous_segment_version_id: uuid.UUID | None = Field(
        alias="expectedPreviousSegmentVersionId", default=None
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanActivationCommand(ContractModel):
    expected_active_plan_version_id: uuid.UUID | None = Field(
        alias="expectedActivePlanVersionId", default=None
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanMaterializeCommand(ContractModel):
    base_plan_version_id: uuid.UUID = Field(alias="basePlanVersionId")
    plan: SeriesPlanDraft
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesEpisodeMaterializeCommand(ContractModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesEpisodeStoryGenerationCommand(ContractModel):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesEpisodeStoryPreviewCommand(ContractModel):
    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)


class SeriesEpisodeStoryPreviewDto(ContractModel):
    series_id: uuid.UUID = Field(alias="seriesId")
    series_plan_version_id: uuid.UUID = Field(alias="seriesPlanVersionId")
    series_episode_id: uuid.UUID = Field(alias="seriesEpisodeId")
    episode_outline_version_id: uuid.UUID = Field(alias="episodeOutlineVersionId")
    project_id: uuid.UUID = Field(alias="projectId")
    incoming_continuity: str | None = Field(alias="incomingContinuity", default=None)
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    prompt_revision: str = Field(alias="promptRevision")


_SERIES_PLAN_KEYS = {"seriesBible", "episodes"}
_SERIES_BIBLE_KEYS = {
    "logline",
    "centralTheme",
    "narrativeMode",
    "worldRules",
    "emotionalArc",
    "recurringLocations",
    "recurringProps",
    "wardrobeRules",
    "continuityRules",
    "visualMotifs",
    "soundMotifs",
    "forbiddenChanges",
}
_EMOTIONAL_ARC_KEYS = {"opening", "development", "climax", "resolution"}
_LOCATION_KEYS = {"key", "name", "description"}
_PROP_KEYS = {"key", "name", "continuityRule"}
_SOURCE_COVERAGE_KEYS = {"sourceUnitOrdinal", "coverage", "coverageNote"}
_EPISODE_KEYS = {
    "order",
    "title",
    "targetDurationSeconds",
    "premise",
    "openingState",
    "trigger",
    "childIntent",
    "childAction",
    "catResponse",
    "visibleChange",
    "endingState",
    "continuityCarryover",
    "recurringLocationKeys",
    "recurringPropKeys",
    "productionWarnings",
    "sourceCoverage",
}


def _retain_known_fields(
    value: dict[str, object], allowed: set[str], *, path: str, extras: list[str]
) -> dict[str, object]:
    retained: dict[str, object] = {}
    for key, item in value.items():
        if key in allowed:
            retained[key] = item
        else:
            extras.append(f"{path}.{key}" if path else key)
    return retained


def _normalize_series_plan_shape(
    payload: dict[str, object], extras: list[str]
) -> dict[str, object] | None:
    normalized = _retain_known_fields(payload, _SERIES_PLAN_KEYS, path="", extras=extras)
    bible = normalized.get("seriesBible")
    episodes = normalized.get("episodes")
    if not isinstance(bible, dict) or not isinstance(episodes, list) or not episodes:
        return None
    clean_bible = _retain_known_fields(
        bible, _SERIES_BIBLE_KEYS, path="seriesBible", extras=extras
    )
    emotional_arc = clean_bible.get("emotionalArc")
    if emotional_arc is not None:
        if not isinstance(emotional_arc, dict):
            return None
        clean_bible["emotionalArc"] = _retain_known_fields(
            emotional_arc,
            _EMOTIONAL_ARC_KEYS,
            path="seriesBible.emotionalArc",
            extras=extras,
        )
    for field, allowed in (
        ("recurringLocations", _LOCATION_KEYS),
        ("recurringProps", _PROP_KEYS),
    ):
        values = clean_bible.get(field)
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            return None
        clean_bible[field] = [
            _retain_known_fields(
                item,
                allowed,
                path=f"seriesBible.{field}.{index}",
                extras=extras,
            )
            for index, item in enumerate(values)
        ]
    clean_episodes: list[dict[str, object]] = []
    for index, episode in enumerate(episodes):
        if not isinstance(episode, dict):
            return None
        clean_episode = _retain_known_fields(
            episode, _EPISODE_KEYS, path=f"episodes.{index}", extras=extras
        )
        source_coverage = clean_episode.get("sourceCoverage")
        if source_coverage is not None:
            if not isinstance(source_coverage, list) or any(
                not isinstance(item, dict) for item in source_coverage
            ):
                return None
            clean_episode["sourceCoverage"] = [
                _retain_known_fields(
                    item,
                    _SOURCE_COVERAGE_KEYS,
                    path=f"episodes.{index}.sourceCoverage.{coverage_index}",
                    extras=extras,
                )
                for coverage_index, item in enumerate(source_coverage)
            ]
        clean_episodes.append(clean_episode)
    normalized["seriesBible"] = clean_bible
    normalized["episodes"] = clean_episodes
    return normalized


def normalize_series_plan_result(
    payload: object,
    *,
    expected_episode_count: int,
    narrative_mode: SeriesNarrativeMode,
    source_unit_ordinals: set[int] | None = None,
    start_episode_order: int = 1,
    require_complete_source_coverage: bool = True,
) -> SeriesPlanNormalizationResult:
    """Preserve paid Provider output while separating parseability from adoption rules."""

    if not isinstance(payload, dict):
        issue = SeriesValidationIssueDto(
            code="invalid_root",
            severity="fatal",
            path="",
            message="模型结果不是可读取的 JSON 对象。",
        )
        return SeriesPlanNormalizationResult({}, None, "invalid", (issue,))
    raw_payload = deepcopy(payload)
    extras: list[str] = []
    normalized = _normalize_series_plan_shape(payload, extras)
    if normalized is None:
        issue = SeriesValidationIssueDto(
            code="invalid_series_structure",
            severity="fatal",
            path="",
            message="模型结果缺少可读取的系列设定或剧集列表。",
        )
        return SeriesPlanNormalizationResult(raw_payload, None, "invalid", (issue,))
    try:
        plan = SeriesPlanDraft.model_validate(normalized)
    except ValidationError as exc:
        issues = tuple(
            SeriesValidationIssueDto(
                code="invalid_field_type",
                severity="fatal",
                path=".".join(str(part) for part in error["loc"]),
                message=str(error["msg"]),
            )
            for error in exc.errors(include_url=False)
        )
        return SeriesPlanNormalizationResult(raw_payload, normalized, "invalid", issues)

    validation_disposition, validation_issues = validate_series_plan(
        plan,
        expected_episode_count=expected_episode_count,
        narrative_mode=narrative_mode,
        source_unit_ordinals=source_unit_ordinals,
        start_episode_order=start_episode_order,
        require_complete_source_coverage=require_complete_source_coverage,
    )
    extra_issues = [
        SeriesValidationIssueDto(
            code="provider_extra_field",
            severity="warning",
            path=path,
            message="模型附带的额外说明已保存在生成记录中，不进入正式系列方案。",
        )
        for path in extras
    ]
    issues = (*validation_issues, *extra_issues)
    return SeriesPlanNormalizationResult(
        raw_payload,
        normalized,
        validation_disposition,
        issues,
        plan,
    )


def validate_series_plan(
    plan: SeriesPlanDraft,
    *,
    expected_episode_count: int,
    narrative_mode: SeriesNarrativeMode,
    source_unit_ordinals: set[int] | None = None,
    start_episode_order: int = 1,
    require_complete_source_coverage: bool = True,
) -> tuple[SeriesPlanDisposition, list[SeriesValidationIssueDto]]:
    issues: list[SeriesValidationIssueDto] = []
    if len(plan.episodes) != expected_episode_count:
        issues.append(
            SeriesValidationIssueDto(
                code="episode_count_mismatch",
                severity="blocking",
                path="episodes",
                message=(
                    f"计划需要 {expected_episode_count} 集，当前结果包含 {len(plan.episodes)} 集。"
                ),
                suggestedAction="补充或移除剧集，使数量与系列设置一致。",
            )
        )
    expected_orders = list(
        range(start_episode_order, start_episode_order + len(plan.episodes))
    )
    actual_orders = [episode.order for episode in plan.episodes]
    if actual_orders != expected_orders:
        issues.append(
            SeriesValidationIssueDto(
                code="episode_order_not_contiguous",
                severity="blocking",
                path="episodes",
                message=f"集数必须从 {start_episode_order} 开始并保持连续。",
                suggestedAction="调整集数顺序。",
            )
        )
    if plan.series_bible.narrative_mode != narrative_mode:
        issues.append(
            SeriesValidationIssueDto(
                code="narrative_mode_changed",
                severity="blocking",
                path="seriesBible.narrativeMode",
                message="规划结果改变了用户确认的叙事模式。",
                suggestedAction="恢复系列设置中的叙事模式。",
            )
        )
    required_bible_fields = {
        "logline": plan.series_bible.logline,
        "centralTheme": plan.series_bible.central_theme,
        "emotionalArc.opening": plan.series_bible.emotional_arc.opening,
        "emotionalArc.development": plan.series_bible.emotional_arc.development,
        "emotionalArc.climax": plan.series_bible.emotional_arc.climax,
        "emotionalArc.resolution": plan.series_bible.emotional_arc.resolution,
    }
    for field, value in required_bible_fields.items():
        if not value.strip():
            issues.append(
                SeriesValidationIssueDto(
                    code="required_content_missing",
                    severity="blocking",
                    path=f"seriesBible.{field}",
                    message="采用整季方案前需要补充该项内容。",
                    suggestedAction="在已保存的方案中补充，不需要重新调用模型。",
                )
            )
    required_episode_fields = (
        ("title", lambda item: item.title),
        ("premise", lambda item: item.premise),
        ("openingState", lambda item: item.opening_state),
        ("trigger", lambda item: item.trigger),
        ("childIntent", lambda item: item.child_intent),
        ("childAction", lambda item: item.child_action),
        ("catResponse", lambda item: item.cat_response),
        ("visibleChange", lambda item: item.visible_change),
        ("endingState", lambda item: item.ending_state),
    )
    for index, episode in enumerate(plan.episodes):
        if not 8 <= episode.target_duration_seconds <= 15:
            issues.append(
                SeriesValidationIssueDto(
                    code="episode_duration_invalid",
                    severity="blocking",
                    path=f"episodes.{index}.targetDurationSeconds",
                    message="每集时长必须为 8–15 秒。",
                    suggestedAction="调整本集目标时长后再采用。",
                )
            )
        for field, read in required_episode_fields:
            if not read(episode).strip():
                issues.append(
                    SeriesValidationIssueDto(
                        code="required_content_missing",
                        severity="blocking",
                        path=f"episodes.{index}.{field}",
                        message="本集的重要内容尚未填写。",
                        suggestedAction="补充该字段后再采用，不需要重新调用模型。",
                    )
                )
    if narrative_mode == "continuous":
        for previous, current in zip(plan.episodes, plan.episodes[1:], strict=False):
            if not current.opening_state.strip() or not previous.ending_state.strip():
                issues.append(
                    SeriesValidationIssueDto(
                        code="continuity_state_missing",
                        severity="blocking",
                        path=f"episodes[{current.order - 1}].openingState",
                        message="连续剧情需要明确上一集结尾与下一集开场。",
                        suggestedAction="补充相邻两集的状态承接。",
                    )
                )
    if source_unit_ordinals:
        coverage_by_ordinal: dict[int, list[int]] = {}
        for episode in plan.episodes:
            for coverage in episode.source_coverage:
                if coverage.source_unit_ordinal not in source_unit_ordinals:
                    issues.append(
                        SeriesValidationIssueDto(
                            code="source_beat_out_of_range",
                            severity="blocking",
                            path=f"episodes.{episode.order - 1}.sourceCoverage",
                            message=(
                                f"来源剧情节拍 {coverage.source_unit_ordinal} 不属于当前系列。"
                            ),
                            suggestedAction="只使用页面列出的安全剧情节拍序号。",
                        )
                    )
                    continue
                coverage_by_ordinal.setdefault(coverage.source_unit_ordinal, []).append(
                    episode.order
                )
        if require_complete_source_coverage:
            for ordinal in sorted(source_unit_ordinals - coverage_by_ordinal.keys()):
                issues.append(
                    SeriesValidationIssueDto(
                        code="source_beat_not_covered",
                        severity="blocking",
                        path="episodes",
                        message=f"来源剧情节拍 {ordinal} 尚未被任何剧集覆盖。",
                        suggestedAction="把该节拍加入相邻剧集，或明确将它标记为仅参考。",
                    )
                )
        for ordinal, orders in coverage_by_ordinal.items():
            if len(orders) > 1 and orders != list(range(min(orders), max(orders) + 1)):
                issues.append(
                    SeriesValidationIssueDto(
                        code="source_beat_non_contiguous_reuse",
                        severity="blocking",
                        path="episodes",
                        message=f"来源剧情节拍 {ordinal} 被不相邻剧集重复引用。",
                        suggestedAction="拆分同一节拍时只能由连续剧集承接。",
                    )
                )
    disposition: SeriesPlanDisposition = (
        "needs_input"
        if any(issue.severity == "blocking" for issue in issues)
        else "candidate_ready"
    )
    return disposition, issues


def compile_series_plan_preview(
    series: StorySeriesDto,
    *,
    source_beats: list[SeriesSourceBeatDto] | None = None,
    canon_profile_hash: str,
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesPlanPreviewDto:
    prompt_revision = "catflow-series-planner-v2"
    source_beats = source_beats or []
    requested_episode_count = min(
        series.planned_episode_count or DEFAULT_ONGOING_PLANNING_BATCH,
        MAX_SERIES_PLANNING_BATCH,
    )
    remaining_episode_count = (
        max(series.planned_episode_count - requested_episode_count, 0)
        if series.planned_episode_count is not None
        else None
    )
    source_section = ""
    if source_beats:
        source_section = (
            "\n【来源剧情节拍】\n"
            + "\n".join(
                f"{beat.binding_order}. {beat.title}：{beat.raw_text}" for beat in source_beats
            )
            + "\n每一集必须通过 sourceCoverage 引用上述安全序号。允许把多个相邻节拍组合为一集，"
            "也允许把一个过长节拍拆到连续多集，但必须说明 whole、partial 或 "
            "continuation 及覆盖内容。"
        )
    prompt = (
        "你是 CatFlow 系列策划。只规划整季系列圣经和逐集简纲，不生成完整剧本、分镜或媒体。\n"
        f"系列：{series.title}\n核心构想：{series.premise}\n"
        f"叙事模式：{series.narrative_mode}\n系列长度：{series.length_mode}\n"
        f"本次规划集数：{requested_episode_count}\n"
        f"每集时长：{series.default_episode_duration_seconds} 秒，9:16，24 fps。\n"
        f"世界设定：{series.world_setting}\n情绪方向：{series.emotional_direction}\n"
        f"结局目标：{series.ending_goal or '由整季路线自然收束'}\n"
        f"贯穿元素：{'、'.join(series.recurring_elements) or '无额外指定'}\n"
        f"必须保留：{'、'.join(series.must_keep) or '固定儿童、猫咪和画风'}\n"
        f"必须避免：{'、'.join(series.must_avoid) or '危险动作和身份漂移'}\n"
        "每集必须能在 8–15 秒内完成一个可见事件，包含开场状态、触发、儿童动作、"
        "猫咪反应、可见变化和结尾状态。连续模式必须写清相邻剧集承接点。"
        f"{source_section}\n"
        f"单次最多规划 {MAX_SERIES_PLANNING_BATCH} 集；这是调用批量边界，不是系列总集数上限。"
    )
    schema = series_plan_output_schema()
    document = {
        "seriesId": str(series.id),
        "series": series.model_dump(mode="json", by_alias=True),
        "canonProfileHash": canon_profile_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "promptRevision": prompt_revision,
        "prompt": prompt,
        "outputSchema": schema,
        "sourceBeats": [beat.model_dump(mode="json", by_alias=True) for beat in source_beats],
        "requestedEpisodeCount": requested_episode_count,
    }
    digest = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SeriesPlanPreviewDto(
        seriesId=series.id,
        provider=provider,
        model=model,
        capabilityRevision=capability_revision,
        inputHash=digest,
        prompt=prompt,
        outputSchema=schema,
        plannedEpisodeCount=requested_episode_count,
        totalPlannedEpisodeCount=series.planned_episode_count,
        remainingEpisodeCount=remaining_episode_count,
        lengthMode=series.length_mode,
        defaultEpisodeDurationSeconds=series.default_episode_duration_seconds,
        promptRevision=prompt_revision,
    )


def series_plan_output_schema() -> dict[str, Any]:
    # The Provider schema keeps only parseability constraints. Exact episode count,
    # continuity and adoption safety are checked after the paid result is preserved.
    return SeriesPlanDraft.model_json_schema(by_alias=True)


def compile_series_plan_segment_preview(
    series: StorySeriesDto,
    *,
    active_plan: SeriesPlanVersionDto,
    command: SeriesPlanSegmentCommand,
    source_beats: list[SeriesSourceBeatDto],
    canon_profile_hash: str,
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesPlanSegmentPreviewDto:
    prompt_revision = "catflow-series-segment-planner-v1"
    end_episode_order = (
        command.start_episode_order + command.requested_episode_count - 1
    )
    remaining_episode_count = (
        max((series.planned_episode_count or 0) - end_episode_order, 0)
        if series.length_mode == "fixed"
        else None
    )
    source_section = "\n".join(
        f"{beat.binding_order}. {beat.title}：{beat.raw_text}" for beat in source_beats
    ) or "本系列没有绑定来源剧情节拍。"
    prompt = (
        "你是 CatFlow 长系列分段策划。当前系列圣经和首段方案已经采用；"
        "本次只规划用户明确指定的下一段，不生成后续段、完整剧本、图片、分镜或视频。\n"
        f"系列：{series.title}\n当前采用方案：版本 {active_plan.revision}\n"
        f"本次范围：第 {command.start_episode_order}–{end_episode_order} 集，"
        f"共 {command.requested_episode_count} 集。\n"
        f"每集约 {series.default_episode_duration_seconds} 秒；"
        f"叙事模式：{series.narrative_mode}。\n"
        "【系列圣经】\n"
        f"{active_plan.plan.series_bible.model_dump_json(by_alias=True)}\n"
        "【来源剧情节拍】\n"
        f"{source_section}\n"
        "剧集 order 必须与本次范围逐一对应。sourceCoverage 只能引用上述安全序号；"
        "允许组合相邻节拍或把过长节拍拆到连续剧集，并明确 whole、partial 或 continuation。"
    )
    schema = series_plan_output_schema()
    document = {
        "seriesId": str(series.id),
        "activePlanVersionId": str(active_plan.id),
        "previousSegmentVersionId": (
            str(command.expected_previous_segment_version_id)
            if command.expected_previous_segment_version_id is not None
            else None
        ),
        "startEpisodeOrder": command.start_episode_order,
        "requestedEpisodeCount": command.requested_episode_count,
        "sourceBeats": [
            beat.model_dump(mode="json", by_alias=True) for beat in source_beats
        ],
        "canonProfileHash": canon_profile_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "promptRevision": prompt_revision,
        "prompt": prompt,
        "outputSchema": schema,
    }
    input_hash = hashlib.sha256(
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return SeriesPlanSegmentPreviewDto(
        seriesId=series.id,
        startEpisodeOrder=command.start_episode_order,
        requestedEpisodeCount=command.requested_episode_count,
        remainingEpisodeCount=remaining_episode_count,
        expectedSeriesPlanVersionId=active_plan.id,
        expectedPreviousSegmentVersionId=command.expected_previous_segment_version_id,
        provider=provider,
        model=model,
        capabilityRevision=capability_revision,
        inputHash=input_hash,
        prompt=prompt,
        outputSchema=schema,
        promptRevision=prompt_revision,
    )


def compile_series_episode_story_preview(
    *,
    series: StorySeriesDto,
    active_plan: SeriesPlanVersionDto,
    episode: SeriesEpisodeDto,
    incoming_continuity: str | None,
    additional_notes: str | None,
    canon_profile_hash: str,
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesEpisodeStoryPreviewDto:
    if episode.project_id is None:
        raise ValueError("series episode must be materialized before story planning")
    prompt_revision = "catflow-series-episode-planner-v1"
    outline = episode.outline
    prompt = (
        "你是 CatFlow 单集故事策划。根据已经采用的整季路线，只扩写当前这一集，"
        "不得生成其他集、分镜、图片或视频。\n"
        f"系列：{series.title}\n整季核心：{active_plan.plan.series_bible.logline}\n"
        f"本集：第 {episode.order} 集《{outline.title}》，"
        f"目标 {outline.target_duration_seconds} 秒。\n"
        f"本集简纲：{outline.premise}\n开场状态：{outline.opening_state}\n"
        f"触发：{outline.trigger}\n儿童目标：{outline.child_intent}\n"
        f"儿童动作：{outline.child_action}\n猫咪回应：{outline.cat_response}\n"
        f"可见变化：{outline.visible_change}\n结尾状态：{outline.ending_state}\n"
        f"进入本集的连续性：{incoming_continuity or '本集不依赖上一集已确认状态'}\n"
        f"用户补充：{additional_notes or '无'}\n"
        "输出一条可在目标时长内完成的生活微事件。标题简短；环境描述只写空间、"
        "天气、道具和光线，不把人物动作混入 environmentIntent；动作写清初始状态、"
        "变化过程和结束状态。保持固定儿童、猫咪身份与系列设定，不擅自改写整季路线。"
    )
    output_schema = LifeStoryProposalDraft.model_json_schema(by_alias=True)
    document = {
        "seriesId": str(series.id),
        "seriesPlanVersionId": str(active_plan.id),
        "seriesEpisodeId": str(episode.id),
        "episodeOutlineVersionId": str(episode.active_outline_version_id),
        "projectId": str(episode.project_id),
        "incomingContinuity": incoming_continuity,
        "additionalNotes": additional_notes,
        "canonProfileHash": canon_profile_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "promptRevision": prompt_revision,
        "prompt": prompt,
        "outputSchema": output_schema,
    }
    input_hash = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SeriesEpisodeStoryPreviewDto(
        seriesId=series.id,
        seriesPlanVersionId=active_plan.id,
        seriesEpisodeId=episode.id,
        episodeOutlineVersionId=episode.active_outline_version_id,
        projectId=episode.project_id,
        incomingContinuity=incoming_continuity,
        provider=provider,
        model=model,
        capabilityRevision=capability_revision,
        inputHash=input_hash,
        prompt=prompt,
        outputSchema=output_schema,
        promptRevision=prompt_revision,
    )
