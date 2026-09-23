"""故事来源导入(粘贴 / txt / md)的应用层 —— 剧情节拍分析的 DTO、prompt 编译与结果规范化。

本文件承载"故事原文 → 结构化来源"流程的第一段:

1. 导入命令与 DTO:预览命令(StoryImportPreviewCommand)、创建/重分析付费命令、
   来源文档 / 来源单元(剧情节拍)/ 关系建议的草稿与持久化 DTO,
   以及确认后的生产目标(StoryProductionTarget);
2. LLM 剧情节拍分析 prompt 的编译(compile_story_import_preview,
   prompt_revision: catflow-story-source-analyzer-v3)——按主题/事件边界识别
   最小剧情节拍,冻结 contentHash / inputHash 供 worker 提交付费任务时对账;
3. 分析结果与关系建议的规范化(normalize_import_relationship_suggestions):
   关系类型收敛为独立短片(independent)/ 新系列(new_series)——追加既有系列
   (append_series)、修订稿(revision)、参考材料(reference)只能由用户在确认
   阶段显式指定目标,模型不得代选。

调用方为 application/service.py(编译预览、对账 hash、确认与物化)与
worker(ark_results.py 取回 Provider 结果后用 StoryImportAnalysisDraft 校验入库)。
系列相关类型(SeriesLengthMode / SeriesNarrativeMode / StorySeriesDto)复用 series.py。
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from catflow.application.job_execution import JobExecutionDto, PaidJobCommand
from catflow.domain.contract import ContractModel

from .series import SeriesLengthMode, SeriesNarrativeMode, StorySeriesDto

# 来源格式:直接粘贴 / 上传 txt / 上传 md(paste 不得带文件名,上传必须带文件名)
StorySourceFormat = Literal["paste", "txt", "md"]
# 来源文档生命周期:待分析 / 分析中 / 已分析 / 用户已确认去向 / 分析失败
StorySourceStatus = Literal["pending", "analyzing", "analyzed", "confirmed", "failed"]
# 关系建议类型:独立短片 / 新系列 / 追加既有系列 / 修订稿 / 参考材料
# —— 后三种需要用户在确认阶段指定既有系列或项目目标
StorySourceRelationType = Literal[
    "independent", "new_series", "append_series", "revision", "reference"
]


class StoryImportPreviewCommand(ContractModel):
    """故事导入预览命令 —— 来源原文(至多 50 万字符)加格式与文件名;
    reject_blank_text 强制原文非空白,并校验格式与文件名的搭配规则。"""

    raw_text: str = Field(alias="rawText", min_length=1, max_length=500_000)
    source_format: StorySourceFormat = Field(alias="sourceFormat")
    file_name: str | None = Field(alias="fileName", default=None, max_length=260)

    @model_validator(mode="after")
    def reject_blank_text(self) -> StoryImportPreviewCommand:
        if not self.raw_text.strip():
            raise ValueError("story source text cannot be blank")
        if self.source_format == "paste" and self.file_name is not None:
            raise ValueError("pasted story text cannot have a file name")
        if self.source_format in {"txt", "md"} and not self.file_name:
            raise ValueError("uploaded story text requires a file name")
        return self


class StoryProductionTarget(ContractModel):
    """一组来源单元落地为系列时的生产目标 —— 集数/每集时长/叙事模式等预置参数;
    改编策略固定为 condense_mainline(导入的长文本按生产目标缩编主线)。"""

    canon_profile_id: uuid.UUID | None = Field(alias="canonProfileId", default=None)
    length_mode: SeriesLengthMode = Field(alias="lengthMode", default="fixed")
    planned_episode_count: int | None = Field(alias="plannedEpisodeCount", default=3, ge=2)
    default_episode_duration_seconds: int = Field(
        alias="defaultEpisodeDurationSeconds", default=15, ge=8, le=60
    )
    narrative_mode: SeriesNarrativeMode = Field(alias="narrativeMode", default="continuous")
    adaptation_policy: Literal["condense_mainline"] = Field(
        alias="adaptationPolicy", default="condense_mainline"
    )
    must_keep: list[str] = Field(alias="mustKeep", default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_length(self) -> StoryProductionTarget:
        if self.length_mode == "fixed" and self.planned_episode_count is None:
            raise ValueError("固定系列需要至少两集；一集请使用独立短片。")
        if self.length_mode == "ongoing" and self.planned_episode_count is not None:
            raise ValueError("持续连载不设置总集数。")
        return self


class StoryProductionTargetsCommand(ContractModel):
    """批量保存生产目标命令 —— 键为 "default" 或关系建议 id;
    expectedUpdatedAt 乐观并发,文档已在别处变化时拒绝保存。"""

    expected_updated_at: datetime = Field(alias="expectedUpdatedAt")
    production_targets: dict[str, StoryProductionTarget] = Field(alias="productionTargets")


class StoryImportCreateCommand(StoryImportPreviewCommand, PaidJobCommand):
    """创建故事来源文档并提交付费分析命令 —— expectedInputHash 对账预览冻结的
    input hash,配合幂等键防重复付费;可随创建预置各关系建议的生产目标。"""

    production_targets: dict[str, StoryProductionTarget] | None = Field(
        alias="productionTargets", default=None
    )

    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class StoryImportReanalyzeCommand(PaidJobCommand):
    """重新分析既有来源文档命令 —— 同样对账 input hash 并以幂等键防重复付费。"""

    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class StoryImportPreviewDto(ContractModel):
    """分析预览 DTO —— contentHash 标识规范化后的原文(内容级幂等去重),
    inputHash 冻结 prompt+schema+模型配置的全部输入(worker 提交时对账),
    并返回 prompt / outputSchema / promptRevision 供界面展示。"""

    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    character_count: int = Field(alias="characterCount")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    prompt_revision: str = Field(alias="promptRevision")


class StorySourceUnitDraft(ContractModel):
    """模型解析出的一个来源单元(剧情节拍)—— ordinal 从 1 起连续编号,
    rawText 保存对应来源内容,analysis 记录事件密度、来源覆盖与前后承接说明。"""

    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    theme: str | None = Field(default=None, max_length=200)
    raw_text: str = Field(alias="rawText", min_length=1)
    analysis: dict[str, Any] = Field(default_factory=dict)


class EpisodeCountRecommendationDto(ContractModel):
    """非阻塞的系列集数建议 —— 最小/首选/最大三档加理由;只是建议,最终集数由用户确认。"""

    minimum_recommended: int = Field(alias="minimumRecommended", ge=1)
    preferred: int = Field(ge=1)
    maximum_recommended: int = Field(alias="maximumRecommended", ge=1)
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_range(self) -> EpisodeCountRecommendationDto:
        if not self.minimum_recommended <= self.preferred <= self.maximum_recommended:
            raise ValueError("episode recommendation must be ordered")
        return self


class StorySourceRelationSuggestionDraft(ContractModel):
    """关系建议草稿 —— 模型对一组来源单元(unitOrdinals)提出的去向建议,
    带置信度与理由;规范化时 relation_type 收敛为 new_series/independent,
    suggestedSeriesId 一律置空(不替用户绑定既有系列)。"""

    relation_type: StorySourceRelationType = Field(alias="relationType")
    unit_ordinals: list[int] = Field(alias="unitOrdinals", min_length=1)
    title: str = Field(min_length=1, max_length=160)
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    suggested_series_id: uuid.UUID | None = Field(alias="suggestedSeriesId", default=None)
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1, max_length=2_000)
    episode_count_recommendation: EpisodeCountRecommendationDto | None = Field(
        alias="episodeCountRecommendation", default=None
    )


class StoryImportAnalysisDraft(ContractModel):
    """LLM 分析完整结果(同时作为发给模型的 outputSchema)—— 来源单元 + 关系建议
    各至少一条;validate_references 强制单元序号从 1 连续,且建议只引用已存在的单元。"""

    units: list[StorySourceUnitDraft] = Field(min_length=1)
    relation_suggestions: list[StorySourceRelationSuggestionDraft] = Field(
        alias="relationSuggestions", min_length=1
    )

    @model_validator(mode="after")
    def validate_references(self) -> StoryImportAnalysisDraft:
        ordinals = [unit.ordinal for unit in self.units]
        if ordinals != list(range(1, len(self.units) + 1)):
            raise ValueError("story source unit ordinals must start at one and be contiguous")
        available = set(ordinals)
        for suggestion in self.relation_suggestions:
            if not set(suggestion.unit_ordinals) <= available:
                raise ValueError("relation suggestion references an unknown source unit")
        return self


class StorySourceUnitDto(StorySourceUnitDraft):
    """已持久化的来源单元 DTO —— 草稿之上附加 id 与所属文档 id。"""

    id: uuid.UUID
    document_id: uuid.UUID = Field(alias="documentId")
    created_at: datetime = Field(alias="createdAt")


class StorySourceRelationSuggestionDto(ContractModel):
    """已持久化的关系建议 DTO —— unitOrdinals 已映射为真实单元 id,
    status 记录 suggested/accepted/rejected 处理状态。"""

    id: uuid.UUID
    document_id: uuid.UUID = Field(alias="documentId")
    relation_type: StorySourceRelationType = Field(alias="relationType")
    unit_ids: list[uuid.UUID] = Field(alias="unitIds")
    title: str
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    suggested_series_id: uuid.UUID | None = Field(alias="suggestedSeriesId", default=None)
    confidence: int
    rationale: str
    episode_count_recommendation: EpisodeCountRecommendationDto | None = Field(
        alias="episodeCountRecommendation", default=None
    )
    status: Literal["suggested", "accepted", "rejected"]
    created_at: datetime = Field(alias="createdAt")


class StorySourceDocumentDto(ContractModel):
    """故事来源文档 DTO —— 原文全文与 contentHash、格式/文件名、分析状态、
    生产目标,以及解析出的全部来源单元与关系建议。"""

    id: uuid.UUID
    content_hash: str = Field(alias="contentHash")
    source_format: StorySourceFormat = Field(alias="sourceFormat")
    file_name: str | None = Field(alias="fileName", default=None)
    raw_text: str = Field(alias="rawText")
    status: StorySourceStatus
    production_targets: dict[str, StoryProductionTarget] | None = Field(
        alias="productionTargets", default=None
    )
    analysis_job_id: uuid.UUID | None = Field(alias="analysisJobId", default=None)
    units: list[StorySourceUnitDto] = Field(default_factory=list)
    relation_suggestions: list[StorySourceRelationSuggestionDto] = Field(
        alias="relationSuggestions", default_factory=list
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class StoryImportAnalysisJobDto(ContractModel):
    """分析任务 DTO —— 执行状态机、所用 Provider/模型、实际用量与成本、计费状态。"""

    execution: JobExecutionDto | None = None
    revision: int = 0
    id: uuid.UUID
    status: Literal[
        "queued",
        "submitting",
        "submitted",
        "polling",
        "storing",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
        "submission_unknown",
    ]
    provider: str | None = None
    model: str | None = None
    actual_usage: dict[str, Any] | None = Field(alias="actualUsage", default=None)
    actual_cost_micros: int | None = Field(alias="actualCostMicros", default=None)
    billing_status: str = Field(alias="billingStatus", default="pending")
    error: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class StoryImportProjectDto(ContractModel):
    """确认导入后创建的项目摘要(id/标题/主题/目标时长)—— 用于回显物化结果。"""

    id: uuid.UUID
    title: str
    theme: str
    target_duration_seconds: int = Field(alias="targetDurationSeconds")


class StoryImportCreateResultDto(ContractModel):
    """创建来源文档的结果 —— 文档本体、本次提交的分析任务(如有)与是否幂等重放。"""

    document: StorySourceDocumentDto
    analysis_job: StoryImportAnalysisJobDto | None = Field(alias="analysisJob", default=None)
    idempotency_replayed: bool = Field(alias="idempotencyReplayed")


class StoryImportConfirmCommand(ContractModel):
    """用户确认某条关系建议去向的命令 —— target 为最终关系类型:
    append_series 必须指定既有系列;revision/reference 须指定系列或项目其一;
    new_series/independent 不得引用既有目标,且 new_series 必须给出长度模式
    (固定集数须 ≥2 集,连载不设集数);全部组合由 validate_target 强制。"""

    canon_profile_id: uuid.UUID | None = Field(alias="canonProfileId", default=None)
    suggestion_id: uuid.UUID = Field(alias="suggestionId")
    target: StorySourceRelationType
    target_series_id: uuid.UUID | None = Field(alias="targetSeriesId", default=None)
    target_project_id: uuid.UUID | None = Field(alias="targetProjectId", default=None)
    series_length_mode: SeriesLengthMode | None = Field(alias="seriesLengthMode", default=None)
    planned_episode_count: int | None = Field(alias="plannedEpisodeCount", default=None)
    default_episode_duration_seconds: int = Field(
        alias="defaultEpisodeDurationSeconds", default=12, ge=8, le=60
    )
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    adaptation_policy: Literal["preserve_all", "condense_mainline"] = Field(
        alias="adaptationPolicy", default="preserve_all"
    )
    must_keep: list[str] = Field(alias="mustKeep", default_factory=list, max_length=30)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)

    @model_validator(mode="after")
    def validate_target(self) -> StoryImportConfirmCommand:
        if self.target == "append_series" and self.target_series_id is None:
            raise ValueError("append_series requires targetSeriesId")
        if self.target in {"revision", "reference"} and not (
            self.target_series_id or self.target_project_id
        ):
            raise ValueError("revision and reference require a target series or project")
        if self.target in {"new_series", "independent"} and (
            self.target_series_id or self.target_project_id
        ):
            raise ValueError("new targets cannot reference an existing series or project")
        if self.target == "append_series" and self.target_project_id is not None:
            raise ValueError("append_series cannot target a project")
        if self.target_series_id is not None and self.target_project_id is not None:
            raise ValueError("a story relationship can have only one target")
        if self.target == "new_series":
            if self.series_length_mode is None:
                raise ValueError("new_series requires seriesLengthMode")
            if self.series_length_mode == "fixed" and (
                self.planned_episode_count is None or self.planned_episode_count < 2
            ):
                raise ValueError("fixed new_series requires plannedEpisodeCount of at least 2")
            if self.series_length_mode == "ongoing" and self.planned_episode_count is not None:
                raise ValueError("ongoing new_series cannot have plannedEpisodeCount")
        elif self.series_length_mode is not None or self.planned_episode_count is not None:
            raise ValueError("series length applies only to new_series")
        return self


def story_import_confirmation_request_snapshot(
    command: StoryImportConfirmCommand,
) -> dict[str, Any]:
    """生成带版本的不可变请求快照,用于幂等对账(剔除幂等键本身)。"""
    return {
        "version": 2,
        "request": command.model_dump(
            mode="json", by_alias=True, exclude={"idempotency_key"}
        ),
    }


def confirmation_request_matches(saved: dict[str, Any], command: StoryImportConfirmCommand) -> bool:
    """比较已保存的确认请求快照与本次命令是否一致(幂等重放判定)。

    v1 快照没有 canonProfileId 字段,补默认值 None 后再比较,保证旧数据可重放。
    """
    prior = dict(saved.get("request", {}))
    if saved.get("version") == 1:
        prior.setdefault("canonProfileId", None)
    return prior == story_import_confirmation_request_snapshot(command)["request"]


class StoryImportMaterializationDto(ContractModel):
    """一次确认的物化记录 —— 选定的关系类型与目标,以及创建/挂接的系列或项目。"""

    id: uuid.UUID
    suggestion_id: uuid.UUID = Field(alias="suggestionId")
    target: StorySourceRelationType
    target_series_id: uuid.UUID | None = Field(alias="targetSeriesId", default=None)
    target_project_id: uuid.UUID | None = Field(alias="targetProjectId", default=None)
    series: StorySeriesDto | None = None
    projects: list[StoryImportProjectDto] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")


def recommended_episode_count(beat_count: int) -> EpisodeCountRecommendationDto:
    """按剧情节拍数给出确定性集数建议区间:最小 ceil(n/2)、首选 ceil(2n/3)、最大 n。

    节拍不直接决定集数 —— 这只是非阻塞的默认建议(模型未提供集数建议时的兜底),
    最终集数由用户确认。
    """
    if beat_count < 1:
        raise ValueError("episode-count recommendation requires at least one story beat")
    return EpisodeCountRecommendationDto(
        minimumRecommended=math.ceil(beat_count / 2),
        preferred=math.ceil(beat_count * 2 / 3),
        maximumRecommended=beat_count,
        rationale=(f"根据 {beat_count} 个剧情节拍提供确定性编排建议；最终集数由用户确认。"),
    )


def normalize_import_relationship_suggestions(
    analysis: StoryImportAnalysisDraft,
) -> StoryImportAnalysisDraft:
    """规范化模型的关系建议:在用户明确选择既有目标之前,Provider 分析保持独立。

    append_series / revision / reference 都需要指向某个具体的既有系列或项目,
    而分析阶段并没有提供既有系列 ID,因此这三类建议一律收敛:
    覆盖多个来源单元 → new_series,单个来源单元 → independent;
    suggestedSeriesId 一律置空;缺少集数建议时按 recommended_episode_count 的
    确定性算法补齐,保证每条建议都带有非阻塞的集数区间。
    """
    normalized = []
    for suggestion in analysis.relation_suggestions:
        relation_type: StorySourceRelationType = suggestion.relation_type
        # 收敛 relation_type:模型不得替用户绑定既有目标 —— append_series/revision/reference
        # 全部改写为新建去向;既有系列/项目只能由用户在确认阶段(StoryImportConfirmCommand)指定
        if relation_type not in {"new_series", "independent"}:
            relation_type = "new_series" if len(suggestion.unit_ordinals) > 1 else "independent"
        beat_count = len(suggestion.unit_ordinals)
        recommendation = suggestion.episode_count_recommendation
        # 模型未给集数建议时按节拍数确定性兜底(ceil(n/2) ~ n,首选 ceil(2n/3))
        if recommendation is None:
            recommendation = recommended_episode_count(beat_count)
        normalized.append(
            suggestion.model_copy(
                update={
                    "relation_type": relation_type,
                    "suggested_series_id": None,
                    "episode_count_recommendation": recommendation,
                }
            )
        )
    return analysis.model_copy(update={"relation_suggestions": normalized})


def compile_story_import_preview(
    command: StoryImportPreviewCommand,
    *,
    provider: str,
    model: str,
    capability_revision: str,
) -> StoryImportPreviewDto:
    """编译故事来源分析 prompt 并冻结为 contentHash / inputHash。

    prompt_revision 为 catflow-story-source-analyzer-v3。原文先做换行规范化
    (CRLF/CR → LF)并去首尾空白,再计算 contentHash(sha256)标识"这份来源文本",
    用于内容级幂等与去重;同一原文在不同平台粘贴得到同一 hash。

    prompt 对模型的要求:
    - 不依赖标题编号、"第X集"或固定分隔符,按主题、事件边界、人物目标、
      时间与地点变化识别最小但有意义、可追溯的剧情节拍;
    - 节拍用于忠实理解来源,不直接决定最终集数(后续可合并相邻节拍为一集,
      也可把过密节拍拆到连续多集,不得为凑数裁剪或机械拆分);
    - 为独立短片、新系列、追加系列、修订稿或参考材料提出关系建议,但不得替用户
      确认任何关系;当前没有提供既有系列或项目 ID,因此禁止输出 append_series /
      revision / reference —— 连续的多个单元应建议 new_series,真正独立的单元才
      建议 independent;
    - 每项关系建议附带非阻塞的集数建议(minimumRecommended/preferred/
      maximumRecommended/rationale)。

    outputSchema 来自 StoryImportAnalysisDraft(来源单元 + 关系建议);最终 prompt、
    schema 与模型配置(provider/model/capabilityRevision/promptRevision)及 contentHash
    一起经确定性 JSON 冻结为 inputHash —— worker 提交付费任务时以
    expectedInputHash 对账,保证预览与实际调用一致(幂等对账)。
    """
    # 换行规范化 + 去首尾空白:保证同一文本跨平台得到同一 contentHash
    normalized_text = command.raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    prompt_revision = "catflow-story-source-analyzer-v3"
    prompt = (
        "分析下面的故事来源文本。不要依赖标题编号、‘第X集’或固定分隔符；"
        "根据主题、事件边界、人物目标、时间与地点变化识别最小但有意义、可追溯的剧情节拍。"
        "原文可能只含一个故事，也可能包含多个主题、续集、修订稿或参考材料。"
        "剧情节拍用于忠实理解来源，不直接决定最终集数；后续可以把多个相邻节拍组合为一集，"
        "也可以把内容密度过高的节拍拆到连续多集。不要为了落入固定数量而裁剪或机械拆分。"
        "保持所有节拍的顺序和主题归属，覆盖原文全部关键事实、道具状态、时间地点变化与结尾，"
        "不得截断、压缩掉事实或添加原文不存在的主要事件。"
        "每个 rawText 保存对应的来源内容；analysis 中说明事件密度、来源覆盖和前后承接。"
        "对每项关系建议给出 minimumRecommended、preferred、maximumRecommended 和 rationale，"
        "这些只是非阻塞的系列集数建议，不是最终决定。"
        "为独立短片、新系列、追加系列、修订稿或参考材料提出关系建议，"
        "但不得替用户确认任何关系。当前没有提供既有系列或项目 ID，"
        "因此不得输出 append_series、revision 或 reference；连续的多个单元应建议 new_series，"
        "真正独立的单元才建议 independent。\n\n【来源原文】\n"
        f"{normalized_text}"
    )
    output_schema = StoryImportAnalysisDraft.model_json_schema(by_alias=True)
    # 冻结文档:原文 hash、来源格式、模型配置与最终 prompt/schema 一起做
    # 确定性 JSON 序列化后取 sha256 —— 即提交付费任务时对账用的 inputHash
    document = {
        "contentHash": content_hash,
        "sourceFormat": command.source_format,
        "fileName": command.file_name,
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
    return StoryImportPreviewDto(
        contentHash=content_hash,
        inputHash=input_hash,
        characterCount=len(normalized_text),
        prompt=prompt,
        outputSchema=output_schema,
        promptRevision=prompt_revision,
    )
