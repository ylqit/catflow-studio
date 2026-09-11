from __future__ import annotations

from dataclasses import replace

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.series import SeriesCreateCommand, SeriesPatchCommand
from catflow.application.service import (
    ProjectCreate,
    StudioIdempotencyInputConflictError,
    StudioService,
)
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


def _eleven_beat_analysis() -> StoryImportAnalysisDraft:
    return StoryImportAnalysisDraft.model_validate(
        {
            "units": [
                {
                    "ordinal": ordinal,
                    "title": f"剧情节拍 {ordinal}",
                    "theme": "森林野餐",
                    "rawText": f"原文中的第 {ordinal} 个连续事件。",
                }
                for ordinal in range(1, 12)
            ],
            "relationSuggestions": [
                {
                    "relationType": "new_series",
                    "unitOrdinals": list(range(1, 12)),
                    "title": "森林野餐",
                    "narrativeMode": "continuous",
                    "confidence": 95,
                    "rationale": "十一条相邻节拍共同构成从准备到返程的连续一天。",
                }
            ],
        }
    )


def test_preview_extracts_event_beats_without_assigning_episode_length() -> None:
    preview = _service().preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )

    assert preview.prompt_revision == "catflow-story-source-analyzer-v3"
    assert "剧情节拍" in preview.prompt
    assert "不直接决定最终集数" in preview.prompt
    assert "一个来源单元必须等于一个 8–15 秒视频" not in preview.prompt


def test_preview_extracts_traceable_beats_without_treating_each_as_one_episode() -> None:
    preview = _service().preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )

    assert preview.prompt_revision == "catflow-story-source-analyzer-v3"
    assert "剧情节拍" in preview.prompt
    assert "不直接决定最终集数" in preview.prompt
    assert "每个来源单元必须独立成为一条 8–15 秒" not in preview.prompt


def test_explicit_project_and_series_creation_never_reuses_matching_titles() -> None:
    service = _service()
    project_command = ProjectCreate(
        title="同名短片", theme="相同主题", targetDurationSeconds=12
    )
    series_command = SeriesCreateCommand.model_validate(
        {
            "title": "同名系列",
            "premise": "相同构想",
            "narrativeMode": "continuous",
            "plannedEpisodeCount": 2,
            "defaultEpisodeDurationSeconds": 12,
            "worldSetting": "同一地点",
            "emotionalDirection": "温暖",
        }
    )

    first_project = service.create_project(project_command)
    second_project = service.create_project(project_command)
    first_series = service.create_story_series(series_command)
    second_series = service.create_story_series(series_command)

    assert first_project.id != second_project.id
    assert first_series.id != second_series.id


def test_each_intentional_import_creates_a_new_document_and_analysis_job() -> None:
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
    duplicate = service.create_story_import(
        command.model_copy(update={"idempotency_key": "different-http-request"})
    )

    assert first.document.id != duplicate.document.id
    assert first.analysis_job is not None
    assert duplicate.analysis_job is not None
    assert duplicate.analysis_job.id != first.analysis_job.id
    assert duplicate.document.content_hash == first.document.content_hash
    assert duplicate.idempotency_replayed is False
    assert len(service.list_story_imports()) == 2


def test_story_import_http_retry_returns_the_same_document_and_job() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    command = StoryImportCreateCommand(
        rawText=SOURCE_TEXT,
        sourceFormat="paste",
        expectedInputHash=preview.input_hash,
        idempotencyKey="same-story-import-request",
    )

    first = service.create_story_import(command)
    replayed = service.create_story_import(command)

    assert first.document.id == replayed.document.id
    assert first.analysis_job is not None
    assert replayed.analysis_job is not None
    assert first.analysis_job.id == replayed.analysis_job.id
    assert replayed.idempotency_replayed is True
    assert len(service.list_story_imports()) == 1


def test_story_import_idempotency_key_cannot_be_reused_for_different_input() -> None:
    service = _service()
    first_preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=first_preview.input_hash,
            idempotencyKey="conflicting-story-import-request",
        )
    )
    changed_text = f"{SOURCE_TEXT}\n新增一段明确不同的故事。"
    changed_preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=changed_text, sourceFormat="paste")
    )

    with pytest.raises(StudioIdempotencyInputConflictError):
        service.create_story_import(
            StoryImportCreateCommand(
                rawText=changed_text,
                sourceFormat="paste",
                expectedInputHash=changed_preview.input_hash,
                idempotencyKey="conflicting-story-import-request",
            )
        )

    assert len(service.list_story_imports()) == 1


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
            seriesLengthMode="fixed",
            plannedEpisodeCount=2,
            idempotencyKey="confirm-forest-series",
        ),
    )

    assert result.series is not None
    assert result.series.title == "森林野餐"
    assert result.series.planned_episode_count == 2
    assert len(service.list_story_series()) == 1
    assert service.list_projects() == []


@pytest.mark.parametrize("must_keep", [[], ["保留来源文本中的核心事件"]])
def test_new_series_preserves_the_confirmed_must_keep_list(must_keep: list[str]) -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey=f"analyze-must-keep-{len(must_keep)}",
        )
    )
    assert created.analysis_job is not None
    analyzed = service.complete_story_import_analysis(created.analysis_job.id, _analysis())
    command = StoryImportConfirmCommand(
        suggestionId=analyzed.relation_suggestions[0].id,
        target="new_series",
        seriesLengthMode="fixed",
        plannedEpisodeCount=2,
        mustKeep=must_keep,
        idempotencyKey=f"confirm-must-keep-{len(must_keep)}",
    )

    result = service.confirm_story_import(analyzed.id, command)

    assert result.series is not None
    assert result.series.must_keep == must_keep


def test_confirmation_replay_compares_the_original_request_after_series_edit() -> None:
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
            idempotencyKey="analyze-stable-confirmation-request",
        )
    )
    assert created.analysis_job is not None
    analyzed = service.complete_story_import_analysis(created.analysis_job.id, _analysis())
    command = StoryImportConfirmCommand(
        suggestionId=analyzed.relation_suggestions[0].id,
        target="new_series",
        seriesLengthMode="fixed",
        plannedEpisodeCount=2,
        adaptationPolicy="condense_mainline",
        mustKeep=[],
        idempotencyKey="stable-confirmation-request",
    )
    first = service.confirm_story_import(analyzed.id, command)
    assert first.series is not None
    service.update_story_series(
        first.series.id,
        SeriesPatchCommand(plannedEpisodeCount=3, mustKeep=["后来编辑的要求"]),
    )

    replay = service.confirm_story_import(analyzed.id, command)

    assert replay.id == first.id
    with pytest.raises(StudioIdempotencyInputConflictError):
        service.confirm_story_import(
            analyzed.id,
            command.model_copy(update={"planned_episode_count": 4}),
        )

    service.update_story_series(
        first.series.id,
        SeriesPatchCommand(
            plannedEpisodeCount=2,
            mustKeep=["保留来源文本中的核心事件"],
        ),
    )
    repository._story_source_confirmation_requests.pop(command.idempotency_key)

    legacy_replay = service.confirm_story_import(analyzed.id, command)

    assert legacy_replay.id == first.id


def test_eleven_beats_recommend_eight_episodes_and_create_a_fixed_series_without_projects() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="analyze-eleven-beats",
        )
    )
    assert created.analysis_job is not None

    analyzed = service.complete_story_import_analysis(
        created.analysis_job.id, _eleven_beat_analysis()
    )
    suggestion = analyzed.relation_suggestions[0]

    assert len(analyzed.units) == 11
    assert suggestion.episode_count_recommendation is not None
    assert suggestion.episode_count_recommendation.model_dump(by_alias=True) == {
        "minimumRecommended": 6,
        "preferred": 8,
        "maximumRecommended": 11,
        "rationale": "根据 11 个剧情节拍提供确定性编排建议；最终集数由用户确认。",
    }

    result = service.confirm_story_import(
        analyzed.id,
        StoryImportConfirmCommand(
            suggestionId=suggestion.id,
            target="new_series",
            seriesLengthMode="fixed",
            plannedEpisodeCount=8,
            idempotencyKey="confirm-eleven-beats-as-eight-episodes",
        ),
    )

    assert result.series is not None
    assert result.series.length_mode == "fixed"
    assert result.series.planned_episode_count == 8
    assert len(service.list_series_source_beats(result.series.id)) == 11
    assert service.list_series_episodes(result.series.id) == []
    assert service.list_projects() == []


def test_one_saved_analysis_can_explicitly_create_two_independent_series() -> None:
    service = _service()
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="analyze-source-once-for-two-series",
        )
    )
    assert created.analysis_job is not None
    analyzed = service.complete_story_import_analysis(
        created.analysis_job.id, _eleven_beat_analysis()
    )
    suggestion = analyzed.relation_suggestions[0]

    first = service.confirm_story_import(
        analyzed.id,
        StoryImportConfirmCommand(
            suggestionId=suggestion.id,
            target="new_series",
            seriesLengthMode="fixed",
            plannedEpisodeCount=9,
            idempotencyKey="first-series-from-one-analysis",
        ),
    )
    second = service.confirm_story_import(
        analyzed.id,
        StoryImportConfirmCommand(
            suggestionId=suggestion.id,
            target="new_series",
            seriesLengthMode="fixed",
            plannedEpisodeCount=8,
            idempotencyKey="second-series-from-one-analysis",
        ),
    )

    assert first.series is not None and second.series is not None
    assert first.series.id != second.series.id
    assert first.series.planned_episode_count == 9
    assert second.series.planned_episode_count == 8
    assert len(service.list_series_source_beats(first.series.id)) == 11
    assert len(service.list_series_source_beats(second.series.id)) == 11
    assert service.list_projects() == []


def test_provider_cannot_automatically_attach_a_new_import_to_existing_content() -> None:
    service = _service()
    existing = service.create_story_series(
        SeriesCreateCommand.model_validate({
            "title": "旧系列",
            "premise": "旧内容",
            "narrativeMode": "continuous",
            "plannedEpisodeCount": 2,
            "defaultEpisodeDurationSeconds": 12,
            "worldSetting": "家中",
            "emotionalDirection": "温暖",
        })
    )
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=SOURCE_TEXT, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=SOURCE_TEXT,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey="provider-must-not-auto-attach",
        )
    )
    assert created.analysis_job is not None
    provider_analysis = StoryImportAnalysisDraft.model_validate(
        {
            "units": [
                {"ordinal": 1, "title": "准备", "rawText": "准备野餐篮。"},
                {"ordinal": 2, "title": "出发", "rawText": "一起出发。"},
            ],
            "relationSuggestions": [
                {
                    "relationType": "append_series",
                    "unitOrdinals": [1, 2],
                    "title": "错误的自动追加建议",
                    "suggestedSeriesId": str(existing.id),
                    "confidence": 90,
                    "rationale": "模型误以为应该自动追加。",
                }
            ],
        }
    )

    analyzed = service.complete_story_import_analysis(
        created.analysis_job.id, provider_analysis
    )

    assert analyzed.relation_suggestions[0].relation_type == "new_series"
    assert analyzed.relation_suggestions[0].suggested_series_id is None
    assert len(service.list_story_series()) == 1


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
