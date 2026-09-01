from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.aigc_canvas import (
    CanvasDiagnostic,
    CreativeStoryCandidate,
    parse_llm_story_candidate_output,
)
from cat_video_generator.domain.contract_base import StrictModel


def _candidate(index: int) -> dict[str, str]:
    return {
        "title": f"候选 {index}",
        "body": f"候选 {index} 的完整正文",
    }


def test_llm_creative_contract_ignores_only_boundary_extras_and_keeps_long_body() -> None:
    long_body = " 正文 " * 10_000

    result = parse_llm_story_candidate_output(
        {
            "candidates": [
                {
                    "title": "  雨后的亮叶  ",
                    "body": long_body,
                    "summary": "  一次雨后发现  ",
                    "inventedByModel": {"confidence": 0.91},
                },
                _candidate(2),
                _candidate(3),
            ],
            "providerCommentary": "not part of the domain candidate",
        }
    )

    first = result.batch.candidates[0]
    assert first.title == "雨后的亮叶"
    assert first.body == long_body.strip()
    assert first.summary == "一次雨后发现"
    assert first.model_dump() == {
        "title": "雨后的亮叶",
        "body": long_body.strip(),
        "summary": "一次雨后发现",
    }
    assert result.diagnostics == []


def test_standard_llm_creative_batch_normalizes_blank_summary_to_none() -> None:
    result = parse_llm_story_candidate_output(
        {
            "candidates": [
                {"title": "候选 1", "body": "正文 1", "summary": "   "},
                _candidate(2),
                _candidate(3),
            ]
        }
    )

    assert result.batch.candidates[0].summary is None


@pytest.mark.parametrize("candidate_count", [1, 2, 4, 5])
def test_llm_creative_batch_accepts_non_three_counts_with_warning(
    candidate_count: int,
) -> None:
    result = parse_llm_story_candidate_output(
        {"candidates": [_candidate(index) for index in range(candidate_count)]}
    )

    assert len(result.batch.candidates) == candidate_count
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "story_candidate_count"
    ]
    assert result.diagnostics[0].severity == "warning"
    assert str(candidate_count) in result.diagnostics[0].message


def test_llm_creative_batch_rejects_more_than_five_candidates() -> None:
    with pytest.raises(ValueError):
        parse_llm_story_candidate_output(
            {"candidates": [_candidate(index) for index in range(6)]}
        )


def test_llm_creative_parser_preserves_unstructured_text_as_editable_candidate() -> None:
    result = parse_llm_story_candidate_output("  小孩和猫在雨前一起收回画纸。  ")

    assert len(result.batch.candidates) == 1
    assert result.batch.candidates[0].body == "小孩和猫在雨前一起收回画纸。"
    assert result.batch.candidates[0].title
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "story_candidate_unstructured",
        "story_candidate_count",
    }


def test_llm_creative_parser_normalizes_legacy_synopsis_and_logline() -> None:
    result = parse_llm_story_candidate_output(
        {
            "title": "  雨前收画  ",
            "synopsis": "  小孩和猫发现雨云，一起救回晾晒的画。  ",
            "logline": "  一场默契的雨前行动。  ",
            "legacyScore": 88,
        }
    )

    assert result.batch.candidates == [
        CreativeStoryCandidate(
            title="雨前收画",
            body="小孩和猫发现雨云，一起救回晾晒的画。",
            summary="一场默契的雨前行动。",
        )
    ]
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "story_candidate_count"
    ]


def test_legacy_llm_creative_candidate_normalizes_blank_summary_to_none() -> None:
    result = parse_llm_story_candidate_output(
        {
            "title": "旧式候选",
            "synopsis": "旧式候选正文",
            "summary": "   ",
        }
    )

    assert result.batch.candidates[0].summary is None


def test_llm_creative_parser_prefers_current_fields_over_legacy_fallbacks() -> None:
    result = parse_llm_story_candidate_output(
        {
            "title": "字段优先级",
            "body": "当前正文",
            "synopsis": "旧式故事梗概",
            "premise": "旧式前提",
            "summary": "当前摘要",
            "logline": "旧式一句话简介",
        }
    )

    candidate = result.batch.candidates[0]
    assert candidate.body == "当前正文"
    assert candidate.summary == "当前摘要"


@pytest.mark.parametrize(
    ("legacy_fields", "expected_body"),
    [
        (
            {"synopsis": "旧式故事梗概", "premise": "旧式故事前提"},
            "旧式故事梗概",
        ),
        ({"premise": "仅有的旧式故事前提"}, "仅有的旧式故事前提"),
    ],
)
def test_llm_creative_parser_uses_legacy_body_fallback_chain(
    legacy_fields: dict[str, str],
    expected_body: str,
) -> None:
    result = parse_llm_story_candidate_output(
        {"title": "旧式候选", **legacy_fields}
    )

    assert result.batch.candidates[0].body == expected_body


@pytest.mark.parametrize(
    "candidate",
    [
        {"title": "缺少正文"},
        {"title": "空白正文", "body": "   "},
        {"title": "   ", "body": "标题不能为空"},
    ],
)
def test_standard_llm_creative_batch_rejects_missing_or_blank_required_text(
    candidate: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        parse_llm_story_candidate_output({"candidates": [candidate]})


def test_standard_batch_validation_error_is_normalized_with_pydantic_cause() -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_llm_story_candidate_output(
            {"candidates": [{"title": "缺少正文"}]}
        )

    assert type(exc_info.value) is ValueError
    assert "LLM 创作候选批次无效" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("   ", "LLM 创作输出不能为空"),
        ({"candidates": []}, "至少包含 1 个候选"),
        ({"title": "空故事", "synopsis": "   ", "premise": ""}, "非空正文"),
    ],
)
def test_llm_creative_parser_rejects_empty_outputs(output: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_llm_story_candidate_output(output)


def test_global_strict_model_still_rejects_extra_fields() -> None:
    class StrictProbe(StrictModel):
        value: str

    with pytest.raises(ValidationError, match="extra_forbidden"):
        StrictProbe(value="kept", inventedByModel="must fail")


def test_canvas_diagnostic_supports_blocker_target_alias_and_rejects_other_severity() -> None:
    diagnostic = CanvasDiagnostic(
        code="story_candidate_body_missing",
        severity="blocker",
        message="候选正文缺失。",
        targetId="candidate-2",
    )

    assert diagnostic.model_dump(by_alias=True) == {
        "code": "story_candidate_body_missing",
        "severity": "blocker",
        "message": "候选正文缺失。",
        "targetId": "candidate-2",
    }
    with pytest.raises(ValidationError):
        CanvasDiagnostic(
            code="story_candidate_unknown",
            severity="info",
            message="不支持的诊断级别。",
        )
