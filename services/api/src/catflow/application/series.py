"""系列(整季)→续段→单集 三级策划的应用层 —— DTO、方案规范化与 LLM 策划 prompt 编译。

本文件承载三部分职责:

1. 三级策划的命令 / DTO / 草稿模型(ContractModel):既是前后端 JSON 契约,
   也是 Provider(模型)输出契约 —— SeriesPlanDraft / SeriesBibleDraft /
   SeriesEpisodeOutlineDraft 等直接作为 outputSchema 发给模型;
2. 模型方案的规范化(normalize_series_plan_result:保留付费输出、形状清洗、
   解析校验与采用校验分离)和各类一致性 hash(settings hash 绑定可编辑设定、
   materialization hash 幂等物化、input hash 冻结生成输入);
3. 三个 LLM 策划 prompt 的编译:
   - compile_series_plan_preview          → 整季方案(catflow-series-planner-v7-performance)
   - compile_series_plan_segment_preview  → 长系列续段方案(catflow-series-segment-planner-v5-contract)
   - compile_series_episode_story_preview → 单集故事扩写(catflow-series-episode-planner-v6-spatial)

调用方为 application/service.py(编译预览、对账 input hash 后提交付费任务)与
worker(ark_results.py 取回 Provider 结果后调用 normalize_series_plan_result 规范化入库)。
prompt 中系列圣经、必须保留要求、来源处理决定等中文分项渲染委托给 series_prompt_text.py;
叙事与表演方向常量(NARRATIVE_DIRECTION / CAT_PERFORMANCE_DIRECTION)来自 creative_direction.py。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from catflow.application.job_execution import PaidJobCommand
from catflow.domain.contract import ContractModel
from catflow.domain.models import LifeStoryProposalDraft

from .creative_direction import CAT_PERFORMANCE_DIRECTION, NARRATIVE_DIRECTION
from .series_prompt_text import (
    render_length_mode,
    render_must_keep,
    render_narrative_mode,
    render_series_bible,
    render_source_treatments,
)

# 叙事模式:连续剧情 / 轻连续 / 单元故事(中文标签见 series_prompt_text 的映射)
SeriesNarrativeMode = Literal["continuous", "lightly_serialized", "anthology"]
# 改编策略:preserve_all=完整保留来源;condense_mainline=按生产目标缩编主线
AdaptationPolicy = Literal["preserve_all", "condense_mainline"]
# 系列长度:fixed=固定集数(必须给 plannedEpisodeCount);ongoing=持续连载(不设总集数)
SeriesLengthMode = Literal["fixed", "ongoing"]
# 方案版本生命周期状态
SeriesPlanStatus = Literal["candidate", "accepted", "rejected", "superseded"]
# 规范化后的处置结论:可作为候选 / 需人工补充后再采用 / 无法解析
SeriesPlanDisposition = Literal["candidate_ready", "needs_input", "invalid"]
# 单集对来源剧情节拍的覆盖程度:完整覆盖 / 部分覆盖 / 承接前集的延续
SourceCoverageMode = Literal["whole", "partial", "continuation"]
# 单次 LLM 调用最多规划的集数 —— 调用批量边界,不是系列总集数上限
MAX_SERIES_PLANNING_BATCH = 30
# 持续连载(未设总集数)时首批默认规划集数
DEFAULT_ONGOING_PLANNING_BATCH = 12
# 方案规范化算法版本 —— 写入 input hash 文档与校验记录,保证旧结果可复现、对账一致
SERIES_NORMALIZATION_REVISION = "series-plan-normalization-v2"


class SeriesCreateCommand(ContractModel):
    """创建系列命令 —— 用户在策划页确认的系列设定:标题/核心构想/叙事与长度模式/
    每集时长/世界设定/情绪方向/结局目标,以及 must_keep(必须保留)、must_avoid(必须避免)
    与 additional_notes(补充制作约束);后续所有策划 prompt 都从这里取材。"""

    canon_profile_id: uuid.UUID | None = Field(alias="canonProfileId", default=None)
    adaptation_policy: AdaptationPolicy = Field(alias="adaptationPolicy", default="preserve_all")
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
    """系列设定局部更新命令 —— 所有字段可选但至少提供一项(require_change 校验)。"""

    planned_episode_count: int | None = Field(alias="plannedEpisodeCount", default=None, ge=2)
    default_episode_duration_seconds: int | None = Field(
        alias="defaultEpisodeDurationSeconds", default=None, ge=8, le=15
    )
    must_keep: list[str] | None = Field(alias="mustKeep", default=None, max_length=30)

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
    """系列完整 DTO —— 创建设定之外附加 id、规范角色档案、当前激活方案版本与集数统计
    (planned/materialized/completed)。"""

    id: uuid.UUID
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    active_plan_version_id: uuid.UUID | None = Field(alias="activePlanVersionId", default=None)
    planned_count: int = Field(alias="plannedCount", default=0)
    materialized_count: int = Field(alias="materializedCount", default=0)
    completed_count: int = Field(alias="completedCount", default=0)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class RecurringLocationDraft(ContractModel):
    """常驻场景草稿 —— key 是单集大纲 recurringLocationKeys 必须引用的标识符。"""

    key: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=800)


class RecurringPropDraft(ContractModel):
    """常驻道具草稿 —— key 供单集大纲 recurringPropKeys 引用;continuityRule 写跨集一致性要求。"""

    key: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=160)
    continuity_rule: str = Field(alias="continuityRule", default="", max_length=800)


class SeriesEmotionalArcDraft(ContractModel):
    """整季情绪弧线 —— 开场 / 发展 / 高潮 / 收束四段,采用前均为必填(validate 检查)。"""

    opening: str = Field(default="", max_length=600)
    development: str = Field(default="", max_length=600)
    climax: str = Field(default="", max_length=600)
    resolution: str = Field(default="", max_length=600)


class SeriesBibleDraft(ContractModel):
    """整季系列圣经草稿 —— LLM 策划输出的整季核心设定(核心一句话/主题/世界规则/
    情绪弧线/常驻场景与道具/服装与连续性规则/视听母题/禁止改动)。
    prompt 内嵌与界面展示时由 series_prompt_text.render_series_bible 渲染成中文分项。"""

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
    """单集对一个来源剧情节拍的覆盖声明 —— sourceUnitOrdinal 只能引用系列绑定的
    安全序号(validate_series_plan 会拦截越界与非连续复用)。"""

    source_unit_ordinal: int = Field(alias="sourceUnitOrdinal", ge=1)
    coverage: SourceCoverageMode
    coverage_note: str = Field(alias="coverageNote", min_length=1, max_length=1_000)


class SeriesEpisodeOutlineDraft(ContractModel):
    """单集大纲草稿 —— LLM 策划为每集输出的一条可在目标时长内完成的可见事件:
    开场状态→触发→儿童目标/动作→猫咪回应→可见变化→结尾状态的因果链,
    附带来源节拍覆盖声明(sourceCoverage)与常驻场景/道具 key 引用。"""

    order: int = Field(default=0, ge=0)
    title: str = Field(default="", max_length=160)
    target_duration_seconds: int = Field(alias="targetDurationSeconds", default=0, ge=0)
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


class SourceTreatmentDraft(ContractModel):
    """缩编主线下对一个来源事件的处理决定 —— retained/merged/simplified/omitted,
    每个来源必须恰好一份,列出实际使用它的集数与理由(与 sourceCoverage 交叉校验)。"""

    source_unit_ordinal: int = Field(alias="sourceUnitOrdinal", ge=1)
    treatment: Literal["retained", "merged", "simplified", "omitted"]
    episode_orders: list[int] = Field(alias="episodeOrders", default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)


class AdaptationRiskDraft(ContractModel):
    """改编风险声明 —— blocking=True 时作为阻塞问题,方案需人工调整后才能采用。"""

    message: str = Field(min_length=1, max_length=2000)
    blocking: bool = True


class PreservedRequirementDraft(ContractModel):
    """用户"必须保留要求"的落实说明 —— 每条 must_keep 须恰好对应一份,
    写明处理方式(handling)与落实到的集数(校验时逐条对账)。"""

    requirement: str = Field(min_length=1)
    handling: str = Field(min_length=1)
    episode_orders: list[int] = Field(alias="episodeOrders", default_factory=list)


class SeriesPlanDraft(ContractModel):
    """系列方案完整草稿 —— 整季/续段策划 LLM 的输出契约(series_plan_output_schema
    即由它生成):系列圣经 + 逐集大纲(至少一集),缩编路线下另附来源处理决定、
    改编风险与必须保留要求的落实说明。"""

    source_treatments: list[SourceTreatmentDraft] = Field(
        alias="sourceTreatments", default_factory=list
    )
    adaptation_risks: list[AdaptationRiskDraft] = Field(
        alias="adaptationRisks", default_factory=list
    )
    preserved_requirements: list[PreservedRequirementDraft] = Field(
        alias="preservedRequirements", default_factory=list
    )

    series_bible: SeriesBibleDraft = Field(alias="seriesBible")
    episodes: list[SeriesEpisodeOutlineDraft] = Field(min_length=1)


class SeriesValidationIssueDto(ContractModel):
    """单条校验/规范化问题 —— code 标识规则、path 指向字段、severity 决定处置结论;
    规范化产生的问题额外带 beforeValue/afterValue 供界面展示改动。"""

    code: str
    severity: Literal["fatal", "blocking", "warning"]
    path: str
    message: str
    suggested_action: str | None = Field(alias="suggestedAction", default=None)
    normalization_revision: str | None = Field(alias="normalizationRevision", default=None)
    before_value: str | None = Field(alias="beforeValue", default=None)
    after_value: str | None = Field(alias="afterValue", default=None)


@dataclass(frozen=True, slots=True)
class SeriesPlanNormalizationResult:
    """付费模型方案的规范化结果 —— 原始 payload 无条件保留(付费输出不丢),
    同时给出形状清洗后的 payload、处置结论与问题列表。

    recoverable 表示方案可继续采用或人工修补;validation_document() 生成
    持久化到方案版本的校验文档。
    """

    raw_payload: dict[str, object]
    normalized_payload: dict[str, object] | None
    disposition: SeriesPlanDisposition
    issues: tuple[SeriesValidationIssueDto, ...]
    plan: SeriesPlanDraft | None = None
    normalization_revision: str | None = None

    @property
    def recoverable(self) -> bool:
        return self.plan is not None and self.disposition != "invalid"

    def validation_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "disposition": self.disposition,
            "recoverable": self.recoverable,
            "issues": [issue.model_dump(mode="json", by_alias=True) for issue in self.issues],
            "normalizationRevision": self.normalization_revision,
        }
        if self.normalized_payload is not None:
            document["normalizedPayload"] = self.normalized_payload
        return document


class SeriesPlanVersionDto(ContractModel):
    """方案版本 DTO —— 每次生成/编辑保存为一个不可变版本;inputHash 冻结生成时的
    prompt+schema 输入,active 标记当前被系列采用的版本。"""

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
    """系列单集 DTO —— order 在系列内全局递增;status 覆盖从大纲到成片的生产管线;
    project_id 在物化(materialize)后指向单集制作项目,outline 为当前激活大纲版本。"""

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
    """项目所属系列上下文 —— 供单集制作页读取系列设定、当前集与全部兄弟剧集。"""

    series: StorySeriesDto
    episode: SeriesEpisodeDto
    episodes: list[SeriesEpisodeDto]


class SeriesSourceBeatDto(ContractModel):
    """系列绑定的来源剧情节拍 DTO(来自故事导入的 source unit)——
    binding_order 是 prompt 与 sourceCoverage 唯一允许引用的"安全序号",
    raw_text 为该节拍的来源原文。"""

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
    """整季方案生成预览 DTO —— 返回冻结的 prompt/schema 与 inputHash(worker 提交
    付费任务时对账,保证所见即所付),settingsInputHash 绑定可编辑设定供本地重校验,
    并给出本批集数 / 系列总集数 / 剩余集数。"""

    series_id: uuid.UUID = Field(alias="seriesId")
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    settings_input_hash: str = Field(alias="settingsInputHash", pattern=r"^[a-f0-9]{64}$")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    planned_episode_count: int = Field(alias="plannedEpisodeCount")
    total_planned_episode_count: int | None = Field(alias="totalPlannedEpisodeCount", default=None)
    remaining_episode_count: int | None = Field(alias="remainingEpisodeCount", default=None)
    length_mode: SeriesLengthMode = Field(alias="lengthMode")
    default_episode_duration_seconds: int = Field(alias="defaultEpisodeDurationSeconds")
    prompt_revision: str = Field(alias="promptRevision")


class SeriesPlanGenerationCommand(PaidJobCommand):
    """提交整季方案生成命令 —— expectedInputHash 必须等于预览返回的 inputHash,
    配合幂等键防止重复付费。"""

    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanSegmentCommand(ContractModel):
    """长系列续段策划请求 —— 指定起始集号与本批集数,并携带期望的整季方案版本与
    上一续段版本(乐观并发:两者任一变化即拒绝,防止基于过期基线续写)。"""

    start_episode_order: int = Field(alias="startEpisodeOrder", ge=1)
    requested_episode_count: int = Field(
        alias="requestedEpisodeCount", ge=1, le=MAX_SERIES_PLANNING_BATCH
    )
    expected_series_plan_version_id: uuid.UUID = Field(alias="expectedSeriesPlanVersionId")
    expected_previous_segment_version_id: uuid.UUID | None = Field(
        alias="expectedPreviousSegmentVersionId", default=None
    )


class SeriesPlanSegmentGenerationCommand(SeriesPlanSegmentCommand, PaidJobCommand):
    """提交续段生成命令 —— 在续段请求之上对账预览冻结的 input hash,防止重复付费。"""

    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanSegmentPreviewDto(ContractModel):
    """续段方案生成预览 DTO —— 冻结的 prompt/schema 与 inputHash,并回显期望的
    整季方案版本 / 上一续段版本与剩余集数(仅固定集数模式)。"""

    series_id: uuid.UUID = Field(alias="seriesId")
    start_episode_order: int = Field(alias="startEpisodeOrder")
    requested_episode_count: int = Field(alias="requestedEpisodeCount")
    remaining_episode_count: int | None = Field(alias="remainingEpisodeCount", default=None)
    expected_series_plan_version_id: uuid.UUID = Field(alias="expectedSeriesPlanVersionId")
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
    """续段方案版本 DTO —— 记录段范围(起始集号/集数)、处置结论与期望基线版本链,
    inputHash 冻结生成输入,激活后其大纲用于物化对应区间的单集。"""

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
    expected_series_plan_version_id: uuid.UUID = Field(alias="expectedSeriesPlanVersionId")
    previous_segment_version_id: uuid.UUID | None = Field(
        alias="previousSegmentVersionId", default=None
    )
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt_revision: str = Field(alias="promptRevision")
    decided_at: datetime | None = Field(alias="decidedAt", default=None)
    created_at: datetime = Field(alias="createdAt")


class SeriesPlanSegmentActivationCommand(ContractModel):
    """激活续段方案命令 —— 校验期望的整季方案版本与上一续段版本未变后再置为 active。"""

    expected_series_plan_version_id: uuid.UUID = Field(alias="expectedSeriesPlanVersionId")
    expected_previous_segment_version_id: uuid.UUID | None = Field(
        alias="expectedPreviousSegmentVersionId", default=None
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanActivationCommand(ContractModel):
    """激活整季方案命令 —— 期望的当前激活版本(首次激活为空)防止并发覆盖。"""

    expected_active_plan_version_id: uuid.UUID | None = Field(
        alias="expectedActivePlanVersionId", default=None
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesPlanMaterializeCommand(ContractModel):
    """物化方案为新候选版本的命令 —— source=saved_result 基于已保存结果重放
    (以 expectedSettingsHash 对账设定未漂移);source=edited 提交用户编辑后的 plan;
    两条路径互斥(validate_source 强制)。"""

    base_plan_version_id: uuid.UUID = Field(alias="basePlanVersionId")
    source: Literal["edited", "saved_result"] = "edited"
    plan: SeriesPlanDraft | None = None
    expected_settings_hash: str | None = Field(
        alias="expectedSettingsHash", default=None, pattern=r"^[a-f0-9]{64}$"
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)

    @model_validator(mode="after")
    def validate_source(self) -> SeriesPlanMaterializeCommand:
        if self.source == "saved_result":
            if "plan" in self.model_fields_set or self.expected_settings_hash is None:
                raise ValueError("saved_result requires expectedSettingsHash and forbids plan")
        elif self.plan is None or self.expected_settings_hash is not None:
            raise ValueError("edited requires plan and does not accept expectedSettingsHash")
        return self


class SeriesEpisodeMaterializeCommand(ContractModel):
    """物化单集命令 —— 为该集创建制作项目(project),仅携带幂等键。"""

    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesEpisodeStoryGenerationCommand(PaidJobCommand):
    """提交单集故事生成命令 —— 对账预览冻结的 input hash,可携带本次扩写的用户补充。"""

    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class SeriesEpisodeStoryPreviewCommand(ContractModel):
    """单集故事预览命令 —— 仅携带用户补充(拼进 prompt 的"用户补充"行)。"""

    additional_notes: str | None = Field(alias="additionalNotes", default=None, max_length=4_000)


class SeriesEpisodeStoryPreviewDto(ContractModel):
    """单集故事生成预览 DTO —— 冻结的 prompt/schema、inputHash,以及系列/方案/单集/
    大纲/项目全部版本 id 与进入本集的连续性,供 worker 提交付费任务时对账。"""

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


# 规范化时各层允许保留的字段白名单(camelCase,与 outputSchema 一致)——
# 模型多给的字段会被剥离并记为 provider_extra_field 警告,不进入正式方案
_SERIES_PLAN_KEYS = {
    "seriesBible",
    "episodes",
    "sourceTreatments",
    "adaptationRisks",
    "preservedRequirements",
}
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
    """按白名单过滤一层字典;被剥离的额外字段路径收集进 extras,供生成警告。"""
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
    """递归清洗模型输出的形状:逐层剥掉白名单外字段,校验嵌套结构类型。

    返回 None 表示形状不可救(缺系列圣经 / 剧集列表为空 / 嵌套类型错误),
    由调用方记为 fatal;能清洗的部分尽量保留,不丢弃付费输出。
    """
    normalized = _retain_known_fields(payload, _SERIES_PLAN_KEYS, path="", extras=extras)
    bible = normalized.get("seriesBible")
    episodes = normalized.get("episodes")
    if not isinstance(bible, dict) or not isinstance(episodes, list) or not episodes:
        return None
    clean_bible = _retain_known_fields(bible, _SERIES_BIBLE_KEYS, path="seriesBible", extras=extras)
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
    adaptation_policy: AdaptationPolicy = "preserve_all",
    expected_duration_seconds: int | None = None,
    must_keep: list[str] | None = None,
    normalization_revision: str | None = None,
) -> SeriesPlanNormalizationResult:
    """Preserve paid Provider output while separating parseability from adoption rules."""

    if normalization_revision not in {None, SERIES_NORMALIZATION_REVISION}:
        raise ValueError(f"unsupported series normalization revision: {normalization_revision}")

    if not isinstance(payload, dict):
        issue = SeriesValidationIssueDto(
            code="invalid_root",
            severity="fatal",
            path="",
            message="模型结果不是可读取的 JSON 对象。",
        )
        return SeriesPlanNormalizationResult(
            {}, None, "invalid", (issue,), normalization_revision=normalization_revision
        )
    # 深拷贝存档原始付费输出 —— 后续所有清洗只作用于副本,原始结果随方案版本持久化
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
        return SeriesPlanNormalizationResult(
            raw_payload, None, "invalid", (issue,), normalization_revision=normalization_revision
        )
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
        return SeriesPlanNormalizationResult(
            raw_payload, normalized, "invalid", issues,
            normalization_revision=normalization_revision,
        )

    # 规范化 v2:缩编主线路线下,模型已声明 merged/simplified 的来源若仍被标为
    # whole(完整覆盖),自动降级为 partial 并记 warning —— 只修正覆盖声明,不改写剧情原文
    normalization_issues: list[SeriesValidationIssueDto] = []
    if normalization_revision == SERIES_NORMALIZATION_REVISION and (
        adaptation_policy == "condense_mainline"
    ):
        episode_orders = [episode.order for episode in plan.episodes]
        treatment_ordinals = [item.source_unit_ordinal for item in plan.source_treatments]
        for treatment in plan.source_treatments:
            ordinal = treatment.source_unit_ordinal
            if (
                treatment.treatment not in {"merged", "simplified"}
                or ordinal not in (source_unit_ordinals or set())
                or treatment_ordinals.count(ordinal) != 1
                or not treatment.reason.strip()
                or not treatment.episode_orders
                or len(set(treatment.episode_orders)) != len(treatment.episode_orders)
                or len(set(episode_orders)) != len(episode_orders)
                or not set(treatment.episode_orders) <= set(episode_orders)
            ):
                continue
            references = [
                (episode_index, coverage_index, episode.order, coverage)
                for episode_index, episode in enumerate(plan.episodes)
                for coverage_index, coverage in enumerate(episode.source_coverage)
                if coverage.source_unit_ordinal == ordinal
            ]
            if (
                {order for _, _, order, _ in references} != set(treatment.episode_orders)
                or len(references) != len(treatment.episode_orders)
                or any(not coverage.coverage_note.strip() for _, _, _, coverage in references)
            ):
                continue
            for episode_index, coverage_index, _, coverage in references:
                if coverage.coverage != "whole":
                    continue
                coverage.coverage = "partial"
                normalized["episodes"][episode_index]["sourceCoverage"][coverage_index][
                    "coverage"
                ] = "partial"
                normalization_issues.append(
                    SeriesValidationIssueDto(
                        code="normalized_source_coverage",
                        severity="warning",
                        path=f"episodes.{episode_index}.sourceCoverage.{coverage_index}.coverage",
                        message="模型已声明合并或简化该来源，覆盖程度按部分覆盖记录；剧情原文未改写。",
                        normalizationRevision=normalization_revision,
                        beforeValue="whole",
                        afterValue="partial",
                    )
                )

    # 形状可解析 ≠ 可采用:集数/连续性/来源覆盖等采用规则单独校验,
    # blocking 问题只会把结论降为 needs_input(可人工修补),不判 invalid
    validation_disposition, validation_issues = validate_series_plan(
        plan,
        expected_episode_count=expected_episode_count,
        narrative_mode=narrative_mode,
        source_unit_ordinals=source_unit_ordinals,
        start_episode_order=start_episode_order,
        require_complete_source_coverage=require_complete_source_coverage,
        adaptation_policy=adaptation_policy,
        expected_duration_seconds=expected_duration_seconds,
        must_keep=must_keep,
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
    issues = (*validation_issues, *normalization_issues, *extra_issues)
    return SeriesPlanNormalizationResult(
        raw_payload,
        normalized,
        validation_disposition,
        issues,
        plan,
        normalization_revision,
    )


def validate_series_plan(
    plan: SeriesPlanDraft,
    *,
    expected_episode_count: int,
    narrative_mode: SeriesNarrativeMode,
    source_unit_ordinals: set[int] | None = None,
    start_episode_order: int = 1,
    require_complete_source_coverage: bool = True,
    adaptation_policy: AdaptationPolicy = "preserve_all",
    expected_duration_seconds: int | None = None,
    must_keep: list[str] | None = None,
) -> tuple[SeriesPlanDisposition, list[SeriesValidationIssueDto]]:
    """校验方案的"采用规则",返回 (处置结论, 问题列表)。

    检查项:集数与序号连续、叙事模式未被改写、圣经与单集必填内容、单集时长 8–15 秒、
    连续模式的相邻承接、sourceCoverage 安全序号与完整覆盖(缩编路线豁免)、
    缩编路线下 sourceTreatments 逐来源对账 / must_keep 落实 / 改编风险升级。
    任一 blocking 问题 → needs_input;否则 candidate_ready。
    可解析性问题不在此处,由 normalize_series_plan_result 前置处理。
    """
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
    expected_orders = list(range(start_episode_order, start_episode_order + len(plan.episodes)))
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
        if require_complete_source_coverage and adaptation_policy != "condense_mainline":
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
    if adaptation_policy == "condense_mainline":
        orders = set(actual_orders)
        treatments = plan.source_treatments
        ordinals = [item.source_unit_ordinal for item in treatments]
        if set(ordinals) != (source_unit_ordinals or set()) or len(ordinals) != len(set(ordinals)):
            issues.append(
                SeriesValidationIssueDto(
                    code="source_treatment_incomplete",
                    severity="blocking",
                    path="sourceTreatments",
                    message="每个来源事件都必须有且只有一份保留、合并、简化或省略的处理说明。",
                )
            )
        for index, item in enumerate(treatments):
            covered = {
                episode.order
                for episode in plan.episodes
                if any(
                    coverage.source_unit_ordinal == item.source_unit_ordinal
                    for coverage in episode.source_coverage
                )
            }
            expected = set(item.episode_orders)
            if (item.treatment == "omitted" and (expected or covered)) or (
                item.treatment != "omitted"
                and (not expected or not expected <= orders or covered != expected)
            ):
                issues.append(
                    SeriesValidationIssueDto(
                        code="source_treatment_conflict",
                        severity="blocking",
                        path=f"sourceTreatments.{index}",
                        message="来源处理与分集引用不一致；省略事件不得计入覆盖。",
                    )
                )
            if item.treatment in {"merged", "simplified"} and any(
                coverage.coverage == "whole"
                for episode in plan.episodes
                for coverage in episode.source_coverage
                if coverage.source_unit_ordinal == item.source_unit_ordinal
            ):
                issues.append(
                    SeriesValidationIssueDto(
                        code="condensed_source_marked_whole",
                        severity="blocking",
                        path=f"sourceTreatments.{index}",
                        message=(
                            "合并或简化事件须使用 partial 并说明实际保留内容，不能标为完整覆盖。"
                        ),
                    )
                )
        for index, episode in enumerate(plan.episodes):
            if (
                expected_duration_seconds is not None
                and episode.target_duration_seconds != expected_duration_seconds
            ):
                issues.append(
                    SeriesValidationIssueDto(
                        code="target_duration_mismatch",
                        severity="blocking",
                        path=f"episodes.{index}.targetDurationSeconds",
                        message=(
                            f"本集必须保持已指定的 {expected_duration_seconds} 秒。"
                            "请精简动作或调整生产目标。"
                        ),
                    )
                )
        for requirement in must_keep or []:
            matches = [
                item for item in plan.preserved_requirements if item.requirement == requirement
            ]
            if (
                len(matches) != 1
                or not matches[0].episode_orders
                or not set(matches[0].episode_orders) <= orders
            ):
                issues.append(
                    SeriesValidationIssueDto(
                        code="must_keep_unaccounted",
                        severity="blocking",
                        path="preservedRequirements",
                        message=f"必须说明如何保留：{requirement}",
                    )
                )
        for index, risk in enumerate(plan.adaptation_risks):
            issues.append(
                SeriesValidationIssueDto(
                    code="adaptation_risk",
                    severity="blocking" if risk.blocking else "warning",
                    path=f"adaptationRisks.{index}",
                    message=risk.message,
                    suggestedAction="调整镜头动作、方案或生产目标；不需要重新分析原文。",
                )
            )
    disposition: SeriesPlanDisposition = (
        "needs_input"
        if any(issue.severity == "blocking" for issue in issues)
        else "candidate_ready"
    )
    return disposition, issues


# 缩编主线(condense_mainline)改编策略的追加策划指令 —— 整季与续段两个 compile 函数
# 在该策略下把本段拼进 prompt 末尾,并把 revision 切换为带 -condense 的变体。
# 核心要求:严格锁定时长与集数;每个来源事件在 sourceTreatments 恰好一份处理决定;
# preservedRequirements 只逐条引用用户的必须保留要求;无法容纳时以 blocking 风险上报。
CONDENSE_PLANNING_INSTRUCTIONS = (
    "\n【按生产目标保留主线并精简】每集必须严格等于指定时长，集数严格等于本次规划数量。"
    "允许合并重复动作、简化次要过程、减少支线与机位，不能只缩短文案却仍安排全部动作。"
    "保留人物目标、关键因果、必须保留的事实及结局；不得加速、擅自改时长或增加集数。"
    "每个来源事件必须在 sourceTreatments 中出现恰好一次，"
    "treatment 为 retained/merged/simplified/omitted，"
    "episodeOrders 列出实际使用它的集数，reason 解释保留内容和删改原因。"
    "省略事件的 episodeOrders 为空且不得进入 sourceCoverage；"
    "合并或简化使用 partial 并说明保留部分。"
    "在 preservedRequirements 中只逐条原样引用【用户必须保留要求】列表中的条目，"
    "说明 handling 与对应 episodeOrders；该列表为空时返回空数组。"
    "不要把故事全文、通用创作规则、时长或输出字段说明当成用户要求列入该数组。"
    "无法容纳、冲突或关键结局无法保留时，在 adaptationRisks 中"
    "明确 blocking=true；保留方案供修改。"
    "动作时长只是规划估计，不能声称保证模型执行成功。"
)


def series_plan_settings_hash(
    series: StorySeriesDto, source_unit_ordinals: set[int]
) -> str:
    """Bind local revalidation to editable settings and the source reference universe."""
    # settings hash 只覆盖用户可编辑的系列设定(SeriesCreateCommand 全部字段加 id /
    # 规范档案)与绑定的来源节拍序号集合 —— 任一变化,基于旧设定的本地重校验 /
    # saved_result 物化对账即失败,提示设定或来源引用已漂移(不影响 prompt 冻结的 inputHash)
    document = {
        "series": series.model_dump(
            mode="json", by_alias=True,
            include=set(SeriesCreateCommand.model_fields) | {"id", "canon_profile_id"},
        ),
        "sourceUnitOrdinals": sorted(source_unit_ordinals),
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def series_plan_materialization_hash(command: SeriesPlanMaterializeCommand) -> str:
    """物化命令的幂等 hash —— saved_result 以"基线版本 + settings hash + 规范化版本"
    对账;edited 以提交的方案全文对账;同 hash 重放不重复建版本。"""
    # Keep the existing edited-version hash so historical idempotent replays remain valid.
    if command.source == "saved_result":
        document = {
            "basePlanVersionId": str(command.base_plan_version_id),
            "source": command.source,
            "expectedSettingsHash": command.expected_settings_hash,
            "normalizationRevision": SERIES_NORMALIZATION_REVISION,
        }
    else:
        assert command.plan is not None  # Validated by the command's source contract.
        document = {
            "basePlanVersionId": str(command.base_plan_version_id),
            "plan": command.plan.model_dump(mode="json", by_alias=True),
        }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compile_series_plan_preview(
    series: StorySeriesDto,
    *,
    source_beats: list[SeriesSourceBeatDto] | None = None,
    canon_profile_hash: str,
    cat_identity: str = "固定同一只灰白虎斑猫",
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesPlanPreviewDto:
    """编译整季(首批)方案策划 prompt 并冻结为 input hash。

    prompt_revision 基线为 catflow-series-planner-v7-performance。

    输入来源:
    - series:用户确认的系列设定(标题/核心构想/叙事与长度模式/世界设定/情绪方向/
      结局目标/必须避免等),其中【用户必须保留要求】(must_keep)经 render_must_keep
      渲染后拼进 prompt;
    - source_beats:系列绑定的来源剧情节拍(可为空)——渲染为【来源剧情节拍】编号列表,
      要求每集 sourceCoverage 只引用这些安全序号,并声明 whole/partial/continuation;
    - canon_profile_hash / cat_identity:规范角色档案 hash 与本次猫咪身份说明。

    改编与补充约束:
    - adaptation_policy=condense_mainline 时追加 CONDENSE_PLANNING_INSTRUCTIONS,
      revision 切换为 catflow-series-planner-v7-condense-performance;
    - additional_notes 非空时拼进【补充制作约束】小节,revision 追加 -notes 后缀;
    - 末尾统一拼入【本次猫咪身份】与【叙事与表演】(NARRATIVE_DIRECTION +
      CAT_PERFORMANCE_DIRECTION,来自 creative_direction),revision 追加 -canon-performance。

    本次批集数 = min(计划总集数或连载默认批, MAX_SERIES_PLANNING_BATCH);
    剩余集数仅固定集数模式返回。最终 prompt + 输出 schema(SeriesPlanDraft)与全部
    生成输入经确定性 JSON 冻结为 inputHash(sha256),worker 提交付费任务时对账,
    保证预览与实际调用一致;settingsInputHash 另绑定可编辑设定供本地重校验。
    """
    prompt_revision = "catflow-series-planner-v7-performance"
    source_beats = source_beats or []
    # 本次批集数:固定集数取计划总数、持续连载取默认批,均不超过单次调用上限
    requested_episode_count = min(
        series.planned_episode_count or DEFAULT_ONGOING_PLANNING_BATCH,
        MAX_SERIES_PLANNING_BATCH,
    )
    remaining_episode_count = (
        max(series.planned_episode_count - requested_episode_count, 0)
        if series.planned_episode_count is not None
        else None
    )
    # 来源节拍渲染为"安全序号"列表(binding_order)——单集大纲只能通过 sourceCoverage
    # 引用这些序号,防止模型虚构或改写来源;无绑定节拍时整节省略
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
        "每集写清参与者、道具数量、初始位置、动作目的和结束状态；物体移动保持同一实例。"
        "区分身份等长期连续性与本集重新设置的场景、姿态、持有物，不把整季行为顺序搬进单集。"
        "继承、调整、重置应遵守已确认决定。主动作按主体、身体部位或工具、接触对象、方向、"
        "可见结果形成因果链，安排必要反应，确保目标时长足够执行。"
        "环境意图区分固定陈设、可移动道具与角色携带物，只描述动作前的环境；"
        "不把携带物额外复制进背景，不把动作结果提前冻结为陈设。"
        "你是 CatFlow 系列策划。只规划整季系列圣经和逐集简纲，不生成完整剧本、分镜或媒体。\n"
        f"系列：{series.title}\n核心构想：{series.premise}\n"
        f"叙事模式：{render_narrative_mode(series.narrative_mode)}\n"
        f"系列长度：{render_length_mode(series.length_mode)}\n"
        f"本次规划集数：{requested_episode_count}\n"
        f"每集时长：{series.default_episode_duration_seconds} 秒，9:16，24 fps。\n"
        f"世界设定：{series.world_setting}\n情绪方向：{series.emotional_direction}\n"
        f"结局目标：{series.ending_goal or '由整季路线自然收束'}\n"
        f"贯穿元素：{'、'.join(series.recurring_elements) or '无额外指定'}\n"
        "【通用创作规则】保留来源核心因果与结局，保持既定儿童、猫咪身份和画风。\n"
        f"【用户必须保留要求】{render_must_keep(series.must_keep)}\n"
        f"必须避免：{'、'.join(series.must_avoid) or '危险动作和身份漂移'}\n"
        "每集必须能在 8–15 秒内完成一个可见事件，包含开场状态、触发、儿童动作、"
        "猫咪反应、可见变化和结尾状态。连续模式必须写清相邻剧集承接点。"
        f"{source_section}\n"
        f"单次最多规划 {MAX_SERIES_PLANNING_BATCH} 集；这是调用批量边界，不是系列总集数上限。"
    )
    if series.adaptation_policy == "condense_mainline":
        prompt_revision = "catflow-series-planner-v7-condense-performance"
        prompt += CONDENSE_PLANNING_INSTRUCTIONS

    if series.additional_notes:
        prompt += f"\n【补充制作约束】\n{series.additional_notes}"
        prompt_revision += "-notes"

    prompt += (
        f"\n【本次猫咪身份】{cat_identity}。外观以本次固定参考为准，"
        "原文保留故事动作与因果，不沿用其他猫咪版本。"
    )
    prompt += "\n【叙事与表演】" + NARRATIVE_DIRECTION + CAT_PERFORMANCE_DIRECTION
    prompt_revision += "-canon-performance"
    schema = series_plan_output_schema()
    # 冻结文档:系列设定全文、来源节拍、规范化版本与最终 prompt/schema 一起
    # 做确定性 JSON 序列化后取 sha256 —— 即 worker 提交时对账用的 inputHash
    document = {
        "seriesId": str(series.id),
        "series": series.model_dump(mode="json", by_alias=True),
        "canonProfileHash": canon_profile_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "promptRevision": prompt_revision,
        "normalizationRevision": SERIES_NORMALIZATION_REVISION,
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
        settingsInputHash=series_plan_settings_hash(
            series, {beat.binding_order for beat in source_beats}
        ),
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
    """整季/续段策划的输出 schema(由 SeriesPlanDraft 生成,发给模型的 outputSchema)。"""
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
    cat_identity: str = "固定同一只灰白虎斑猫",
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesPlanSegmentPreviewDto:
    """编译长系列续段方案策划 prompt 并冻结为 input hash。

    prompt_revision 基线为 catflow-series-segment-planner-v5-contract。
    前提:整季系列圣经与首段方案已激活(active_plan),本次只规划用户明确指定的下一段。

    输入来源:
    - series:系列设定,【用户必须保留要求】(must_keep)渲染后拼进 prompt;
    - active_plan:已激活的整季方案版本 —— 其系列圣经经 render_series_bible
      渲染为【系列圣经】小节,版本号写入 prompt;
    - command:段范围(start_episode_order / requested_episode_count)与期望的
      整季方案版本、上一续段版本 —— 乐观并发基线同时冻结进 input hash;
    - source_beats:系列绑定的全部来源剧情节拍(无绑定时渲染占位说明),
      剧集 order 必须与本次范围逐一对应,sourceCoverage 只能引用节拍安全序号。

    adaptation_policy=condense_mainline 时追加 CONDENSE_PLANNING_INSTRUCTIONS,
    revision 切换为 catflow-series-segment-planner-v5-condense-contract;
    末尾统一拼入猫咪身份与叙事/表演方向(-canon-performance 后缀)。
    续段命令本身不接受 additional_notes(区别于整季预览)。
    最终 prompt + schema(SeriesPlanDraft)与全部输入经确定性 JSON 冻结为
    inputHash,供 worker 提交付费任务时对账;剩余集数仅固定集数模式返回。
    """
    prompt_revision = "catflow-series-segment-planner-v5-contract"
    end_episode_order = command.start_episode_order + command.requested_episode_count - 1
    # 仅固定集数模式计算"规划完本段后还剩多少集";持续连载不承诺总数
    remaining_episode_count = (
        max((series.planned_episode_count or 0) - end_episode_order, 0)
        if series.length_mode == "fixed"
        else None
    )
    source_section = (
        "\n".join(f"{beat.binding_order}. {beat.title}：{beat.raw_text}" for beat in source_beats)
        or "本系列没有绑定来源剧情节拍。"
    )
    prompt = (
        "每集写清参与者、道具数量、初始位置、动作目的和结束状态；物体移动保持同一实例。"
        "区分身份等长期连续性与本集重新设置的场景、姿态、持有物，不把整季行为顺序搬进单集。"
        "继承、调整、重置应遵守已确认决定。主动作按主体、身体部位或工具、接触对象、方向、"
        "可见结果形成因果链，安排必要反应，确保目标时长足够执行。"
        "环境意图区分固定陈设、可移动道具与角色携带物，只描述动作前的环境；"
        "不把携带物额外复制进背景，不把动作结果提前冻结为陈设。"
        "你是 CatFlow 长系列分段策划。当前系列圣经和首段方案已经采用；"
        "本次只规划用户明确指定的下一段，不生成后续段、完整剧本、图片、分镜或视频。\n"
        f"系列：{series.title}\n当前采用方案：版本 {active_plan.revision}\n"
        f"本次范围：第 {command.start_episode_order}–{end_episode_order} 集，"
        f"共 {command.requested_episode_count} 集。\n"
        f"每集约 {series.default_episode_duration_seconds} 秒；"
        f"叙事模式：{render_narrative_mode(series.narrative_mode)}。\n"
        f"【系列圣经】\n{render_series_bible(active_plan.plan.series_bible)}\n"
        "【来源剧情节拍】\n"
        f"{source_section}\n"
        "剧集 order 必须与本次范围逐一对应。sourceCoverage 只能引用上述安全序号；"
        "允许组合相邻节拍或把过长节拍拆到连续剧集，并明确 whole、partial 或 continuation。"
        "\n【通用创作规则】保留来源核心因果与结局，保持既定儿童、猫咪身份和画风。\n"
        f"【用户必须保留要求】{render_must_keep(series.must_keep)}\n"
    )
    if series.adaptation_policy == "condense_mainline":
        prompt_revision = "catflow-series-segment-planner-v5-condense-contract"
        prompt += CONDENSE_PLANNING_INSTRUCTIONS
    prompt += (
        f"\n【本次猫咪身份】{cat_identity}。外观以本次固定参考为准，"
        "原文保留故事动作与因果，不沿用其他猫咪版本。"
    )
    prompt += "\n【叙事与表演】" + NARRATIVE_DIRECTION + CAT_PERFORMANCE_DIRECTION
    prompt_revision += "-canon-performance"
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
        "sourceBeats": [beat.model_dump(mode="json", by_alias=True) for beat in source_beats],
        "canonProfileHash": canon_profile_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "promptRevision": prompt_revision,
        "normalizationRevision": SERIES_NORMALIZATION_REVISION,
        "prompt": prompt,
        "outputSchema": schema,
    }
    input_hash = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
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
    source_segment: SeriesPlanSegmentVersionDto | None = None,
    canon_profile_hash: str,
    cat_identity: str = "固定同一只灰白虎斑猫",
    provider: str,
    model: str,
    capability_revision: str,
) -> SeriesEpisodeStoryPreviewDto:
    """编译单集故事扩写 prompt 并冻结为 input hash。

    prompt_revision 基线为 catflow-series-episode-planner-v6-spatial。
    前提:该集已物化(episode.project_id 非空),否则抛 ValueError。

    输入来源:
    - series / active_plan:系列设定与已激活整季方案 —— 系列圣经经
      render_series_bible 渲染进 prompt,模型只扩写当前一集、不得改写整季路线;
    - episode:本集当前激活大纲 —— 开场状态/触发/儿童目标与动作/猫咪回应/
      可见变化/结尾状态逐项拼入,要求输出可在目标时长内完成的生活微事件;
    - incoming_continuity:上一集确认后进入本集的连续性说明(为空时渲染为
      "本集不依赖上一集已确认状态");
    - additional_notes:用户本次补充,拼进"用户补充"行(整季预览才使用
      【补充制作约束】小节,单集不使用)。

    adaptation_policy=condense_mainline 时 revision 切换为
    catflow-series-episode-planner-v6-condense-spatial,并拼入【已确认缩编决定】:
    来源处理决定优先取 source_segment(续段方案)的 source_treatments,未提供续段时
    回退 active_plan,经 render_source_treatments 渲染并回显 must_keep。
    末尾统一拼入猫咪身份与叙事/表演方向(-canon-performance 后缀)。
    输出 schema 为单集故事 LifeStoryProposalDraft;最终 prompt + schema 与
    系列/方案/单集/大纲/项目全部版本 id 冻结为 inputHash,供 worker 提交时对账。
    """
    if episode.project_id is None:
        raise ValueError("series episode must be materialized before story planning")
    prompt_revision = "catflow-series-episode-planner-v6-spatial"
    outline = episode.outline
    prompt = (
        "每集写清参与者、道具数量、初始位置、动作目的和结束状态；物体移动保持同一实例。"
        "区分身份等长期连续性与本集重新设置的场景、姿态、持有物，不把整季行为顺序搬进单集。"
        "继承、调整、重置应遵守已确认决定。主动作按主体、身体部位或工具、接触对象、方向、"
        "可见结果形成因果链，安排必要反应，确保目标时长足够执行。"
        "环境意图区分固定陈设、可移动道具与角色携带物，只描述动作前的环境；"
        "不把携带物额外复制进背景，不把动作结果提前冻结为陈设。"
        "你是 CatFlow 单集故事策划。根据已经采用的整季路线，只扩写当前这一集，"
        "不得生成其他集、分镜、图片或视频。\n"
        f"系列：{series.title}\n"
        f"整季设定：\n{render_series_bible(active_plan.plan.series_bible)}\n"
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
    if series.adaptation_policy == "condense_mainline":
        prompt_revision = "catflow-series-episode-planner-v6-condense-spatial"
        source_treatments = (
            source_segment.plan if source_segment is not None else active_plan.plan
        ).source_treatments
        prompt += (
            "\n【已确认缩编决定】遵守本集简纲的动作数量和目标时长，不把原文已省略的动作重新加回。"
            f"必须保留：{render_must_keep(series.must_keep)}。"
            f"来源处理：\n{render_source_treatments(source_treatments)}"
            f"\n来源方案：{source_segment.id if source_segment is not None else active_plan.id}"
        )
    prompt += (
        f"\n【本次猫咪身份】{cat_identity}。外观以本次固定参考为准，"
        "原文保留故事动作与因果，不沿用其他猫咪版本。"
    )
    prompt += "\n【叙事与表演】" + NARRATIVE_DIRECTION + CAT_PERFORMANCE_DIRECTION
    prompt_revision += "-canon-performance"
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
