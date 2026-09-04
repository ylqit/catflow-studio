from __future__ import annotations

from dataclasses import replace

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import ProjectCreate, StudioService
from catflow.application.story_imports import (
    StoryImportAnalysisDraft,
    StoryImportConfirmCommand,
    StoryImportCreateCommand,
    StoryImportPreviewCommand,
    StoryImportReanalyzeCommand,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository

SOURCE_TEXT = """主题一：森林野餐
剧本1：准备野餐
孩子整理野餐篮，猫咪放入毛线球。

剧本2：快乐野餐
孩子和猫咪在森林草地野餐。

主题二：下雨天
窗户上的画
孩子在水雾上画猫脸，猫咪留下爪印。
"""


def _service() -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )


def _analysis() -> StoryImportAnalysisDraft:
    return StoryImportAnalysisDraft.model_validate(
        {
            "units": [
                {
                    "ordinal": 1,
                    "title": "准备野餐",
                    "theme": "森林野餐",
                    "rawText": "孩子整理野餐篮，猫咪放入毛线球。",
                    "analysis": {"estimatedMicroShorts": 2},
                },
                {
                    "ordinal": 2,
                    "title": "快乐野餐",
                    "theme": "森林野餐",
                    "rawText": "孩子和猫咪在森林草地野餐。",
                    "analysis": {"estimatedMicroShorts": 1},
                },
                {
                    "ordinal": 3,
                    "title": "窗户上的画",
                    "theme": "下雨天",
                    "rawText": "孩子在水雾上画猫脸，猫咪留下爪印。",
                    "analysis": {"estimatedMicroShorts": 1},
                },
            ],
            "relationSuggestions": [
                {
                    "relationType": "new_series",
                    "unitOrdinals": [1, 2],
                    "title": "森林野餐",
                    "narrativeMode": "continuous",
                    "confidence": 92,
                    "rationale": "两段共享道具并形成前后承接。",
                },
                {
                    "relationType": "new_series",
                    "unitOrdinals": [3],
                    "title": "下雨天",
                    "narrativeMode": "anthology",
                    "confidence": 86,
                    "rationale": "独立雨天微事件。",
                },
            ],
        }
    )


def _micro_short_analysis() -> StoryImportAnalysisDraft:
    return StoryImportAnalysisDraft.model_validate(
        {
            "units": [
                {
                    "ordinal": 1,
                    "title": "装好野餐篮",
                    "theme": "森林野餐",
                    "rawText": "孩子把食物和野餐垫装进篮子。",
                },
                {
                    "ordinal": 2,
                    "title": "猫咪带上毛线球",
                    "theme": "森林野餐",
                    "rawText": "猫咪把毛线球放进篮子，孩子摸摸它。",
                },
                {
                    "ordinal": 3,
                    "title": "一起出发",
                    "theme": "森林野餐",
                    "rawText": "孩子背上背包，猫咪跟着走出家门。",
                },
            ],
            "relationSuggestions": [
                {
                    "relationType": "new_series",
                    "unitOrdinals": [1, 2, 3],
                    "title": "森林野餐",
                    "narrativeMode": "continuous",
                    "confidence": 95,
                    "rationale": "三个微事件按准备顺序连续推进。",
                }
            ],
        }
    )


def test_preview_requires_event_level_micro_short_splitting() -> None:
    preview = _service().preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )

    assert preview.prompt_revision == "catflow-story-source-analyzer-v2"
    assert "8–15 秒" in preview.prompt
    assert "一个主要可见事件" in preview.prompt
    assert "必须继续拆成多个相邻单元" in preview.prompt
    assert "过长内容只标记需要拆分" not in preview.prompt


def test_one_document_creates_one_analysis_job_and_exact_duplicate_reuses_it() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    command = StoryImportCreateCommand(
        rawText=SOURCE_TEXT,
        sourceFormat="paste",
        expectedInputHash=preview.input_hash,
        idempotencyKey="import-multiple-themes",
    )

    first = service.create_story_import(command)
    duplicate_preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    duplicate = service.create_story_import(
        command.model_copy(update={"idempotency_key": "different-http-request"})
    )

    assert first.document.id == duplicate.document.id
    assert first.analysis_job is not None
    assert duplicate.analysis_job is not None
    assert duplicate.analysis_job.id == first.analysis_job.id
    assert duplicate_preview.duplicate_document_id == first.document.id
    assert duplicate.reused is True


def test_failed_analysis_can_restart_on_the_same_document_idempotently() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(
        repository,
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="initial-story-analysis",
        )
    )
    assert created.analysis_job is not None
    failed_at = created.analysis_job.updated_at
    repository._jobs[created.analysis_job.id] = repository._jobs[
        created.analysis_job.id
    ].model_copy(
        update={
            "status": "failed",
            "error": {"code": "provider_failed", "message": "temporary failure"},
            "updated_at": failed_at,
        }
    )

    failed_document = service.get_story_import(created.document.id)
    assert failed_document.status == "failed"

    command = StoryImportReanalyzeCommand(
        expectedInputHash=preview.input_hash,
        idempotencyKey="retry-story-analysis",
    )
    first_retry = service.reanalyze_story_import(created.document.id, command)
    same_retry = service.reanalyze_story_import(created.document.id, command)

    assert first_retry.id == same_retry.id
    assert first_retry.id != created.analysis_job.id
    assert first_retry.story_source_document_id == created.document.id
    assert len(service.list_story_imports()) == 1
    assert service.get_story_import(created.document.id).status == "analyzing"


def test_analysis_preserves_source_units_until_user_confirms_relationship() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="analyze-source-units",
        )
    )
    assert created.analysis_job is not None

    analyzed = service.complete_story_import_analysis(created.analysis_job.id, _analysis())

    assert [unit.raw_text for unit in analyzed.units] == [
        "孩子整理野餐篮，猫咪放入毛线球。",
        "孩子和猫咪在森林草地野餐。",
        "孩子在水雾上画猫脸，猫咪留下爪印。",
    ]
    assert len(analyzed.relation_suggestions) == 2
    assert service.list_story_series() == []

    first_suggestion = analyzed.relation_suggestions[0]
    result = service.confirm_story_import(
        analyzed.id,
        StoryImportConfirmCommand(
            suggestionId=first_suggestion.id,
            target="new_series",
            idempotencyKey="confirm-forest-series",
        ),
    )

    assert result.series is not None
    assert result.series.title == "森林野餐"
    assert result.series.planned_episode_count == 2
    assert len(service.list_story_series()) == 1
    assert service.list_projects() == []


def test_unconfirmed_analysis_can_be_reanalyzed_without_discarding_the_old_result_first() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="initial-unconfirmed-analysis",
        )
    )
    assert created.analysis_job is not None
    first = service.complete_story_import_analysis(created.analysis_job.id, _analysis())

    retry = service.reanalyze_story_import(
        first.id,
        StoryImportReanalyzeCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="reanalyze-unconfirmed-analysis",
        ),
    )
    while_running = service.get_story_import(first.id)

    assert while_running.status == "analyzing"
    assert [unit.title for unit in while_running.units] == [
        "准备野餐",
        "快乐野餐",
        "窗户上的画",
    ]

    replaced = service.complete_story_import_analysis(retry.id, _micro_short_analysis())

    assert [unit.title for unit in replaced.units] == [
        "装好野餐篮",
        "猫咪带上毛线球",
        "一起出发",
    ]
    assert len(replaced.relation_suggestions) == 1
    assert replaced.relation_suggestions[0].relation_type == "new_series"


def test_revision_relationship_links_existing_project_without_overwriting_it() -> None:
    service = _service()
    project = service.create_project(
        ProjectCreate(title="窗户上的画", theme="原故事", targetDurationSeconds=12)
    )
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText="窗户上的画修订稿", sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText="窗户上的画修订稿",
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="analyze-project-revision",
        )
    )
    assert created.analysis_job is not None
    analyzed = service.complete_story_import_analysis(
        created.analysis_job.id,
        StoryImportAnalysisDraft.model_validate(
            {
                "units": [
                    {
                        "ordinal": 1,
                        "title": "窗户上的画修订稿",
                        "rawText": "窗户上的画修订稿",
                    }
                ],
                "relationSuggestions": [
                    {
                        "relationType": "revision",
                        "unitOrdinals": [1],
                        "title": "作为已有短片的修订材料",
                        "confidence": 91,
                        "rationale": "标题和事件与已有项目一致。",
                    }
                ],
            }
        ),
    )

    result = service.confirm_story_import(
        analyzed.id,
        StoryImportConfirmCommand(
            suggestionId=analyzed.relation_suggestions[0].id,
            target="revision",
            targetProjectId=project.id,
            idempotencyKey="confirm-project-revision",
        ),
    )

    assert result.target_project_id == project.id
    assert result.projects == []
    assert service.get_project(project.id).theme == "原故事"
