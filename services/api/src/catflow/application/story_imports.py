from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel

from .series import SeriesNarrativeMode, StorySeriesDto

StorySourceFormat = Literal["paste", "txt", "md"]
StorySourceStatus = Literal["pending", "analyzing", "analyzed", "confirmed", "failed"]
StorySourceRelationType = Literal[
    "independent", "new_series", "append_series", "revision", "reference"
]


class StoryImportPreviewCommand(ContractModel):
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


class StoryImportCreateCommand(StoryImportPreviewCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class StoryImportReanalyzeCommand(ContractModel):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class StoryImportPreviewDto(ContractModel):
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    character_count: int = Field(alias="characterCount")
    duplicate_document_id: uuid.UUID | None = Field(alias="duplicateDocumentId", default=None)
    prompt: str
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    prompt_revision: str = Field(alias="promptRevision")


class StorySourceUnitDraft(ContractModel):
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    theme: str | None = Field(default=None, max_length=200)
    raw_text: str = Field(alias="rawText", min_length=1)
    analysis: dict[str, Any] = Field(default_factory=dict)


class StorySourceRelationSuggestionDraft(ContractModel):
    relation_type: StorySourceRelationType = Field(alias="relationType")
    unit_ordinals: list[int] = Field(alias="unitOrdinals", min_length=1)
    title: str = Field(min_length=1, max_length=160)
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    suggested_series_id: uuid.UUID | None = Field(alias="suggestedSeriesId", default=None)
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1, max_length=2_000)


class StoryImportAnalysisDraft(ContractModel):
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
    id: uuid.UUID
    document_id: uuid.UUID = Field(alias="documentId")
    created_at: datetime = Field(alias="createdAt")


class StorySourceRelationSuggestionDto(ContractModel):
    id: uuid.UUID
    document_id: uuid.UUID = Field(alias="documentId")
    relation_type: StorySourceRelationType = Field(alias="relationType")
    unit_ids: list[uuid.UUID] = Field(alias="unitIds")
    title: str
    narrative_mode: SeriesNarrativeMode | None = Field(alias="narrativeMode", default=None)
    suggested_series_id: uuid.UUID | None = Field(alias="suggestedSeriesId", default=None)
    confidence: int
    rationale: str
    status: Literal["suggested", "accepted", "rejected"]
    created_at: datetime = Field(alias="createdAt")


class StorySourceDocumentDto(ContractModel):
    id: uuid.UUID
    content_hash: str = Field(alias="contentHash")
    source_format: StorySourceFormat = Field(alias="sourceFormat")
    file_name: str | None = Field(alias="fileName", default=None)
    raw_text: str = Field(alias="rawText")
    status: StorySourceStatus
    analysis_job_id: uuid.UUID | None = Field(alias="analysisJobId", default=None)
    units: list[StorySourceUnitDto] = Field(default_factory=list)
    relation_suggestions: list[StorySourceRelationSuggestionDto] = Field(
        alias="relationSuggestions", default_factory=list
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class StoryImportAnalysisJobDto(ContractModel):
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
    id: uuid.UUID
    title: str
    theme: str
    target_duration_seconds: int = Field(alias="targetDurationSeconds")


class StoryImportCreateResultDto(ContractModel):
    document: StorySourceDocumentDto
    analysis_job: StoryImportAnalysisJobDto | None = Field(alias="analysisJob", default=None)
    reused: bool


class StoryImportConfirmCommand(ContractModel):
    suggestion_id: uuid.UUID = Field(alias="suggestionId")
    target: StorySourceRelationType
    target_series_id: uuid.UUID | None = Field(alias="targetSeriesId", default=None)
    target_project_id: uuid.UUID | None = Field(alias="targetProjectId", default=None)
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
        return self


class StoryImportMaterializationDto(ContractModel):
    id: uuid.UUID
    suggestion_id: uuid.UUID = Field(alias="suggestionId")
    target: StorySourceRelationType
    target_series_id: uuid.UUID | None = Field(alias="targetSeriesId", default=None)
    target_project_id: uuid.UUID | None = Field(alias="targetProjectId", default=None)
    series: StorySeriesDto | None = None
    projects: list[StoryImportProjectDto] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")


def compile_story_import_preview(
    command: StoryImportPreviewCommand,
    *,
    duplicate_document_id: uuid.UUID | None,
    provider: str,
    model: str,
    capability_revision: str,
) -> StoryImportPreviewDto:
    normalized_text = command.raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    prompt_revision = "catflow-story-source-analyzer-v2"
    prompt = (
        "分析下面的故事来源文本。不要依赖标题编号、‘第X集’或固定分隔符；"
        "根据主题、事件边界、人物目标、时间与地点变化识别语义单元。"
        "原文可能只含一个故事，也可能包含多个主题、续集、修订稿或参考材料。"
        "每个输出单元必须对应一条可独立制作的 8–15 秒竖屏微短片，并且只包含一个主要可见事件。"
        "标题或段落只是来源边界提示，不是最终集数；如果一个段落依次包含准备、发现、互动、"
        "转场、收拾或离开等多个可观察事件，必须继续拆成多个相邻单元。"
        "短而完整的单一事件不要机械拆分；较长段落通常可以拆成 2–4 个单元，具体数量由事件边界决定。"
        "保持所有单元的顺序和主题归属，覆盖原文全部关键事实、道具状态、时间地点变化与结尾，"
        "不得截断、压缩掉事实或添加原文不存在的主要事件。"
        "每个 rawText 只放与该微短片有关的原文内容；analysis 中说明预计时长、来源覆盖和前后承接。"
        "为独立短片、新系列、追加系列、修订稿或参考材料提出关系建议，"
        "但不得替用户确认任何关系。当前没有提供既有系列或项目 ID，"
        "因此不得输出 append_series、revision 或 reference；连续的多个单元应建议 new_series，"
        "真正独立的单元才建议 independent。\n\n【来源原文】\n"
        f"{normalized_text}"
    )
    output_schema = StoryImportAnalysisDraft.model_json_schema(by_alias=True)
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
        duplicateDocumentId=duplicate_document_id,
        prompt=prompt,
        outputSchema=output_schema,
        promptRevision=prompt_revision,
    )
