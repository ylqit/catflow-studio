from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from volcenginesdkarkruntime._exceptions import ArkAPIConnectionError

from cat_video_generator.application.ports import (
    CreativeDirectorResult,
)
from cat_video_generator.domain.aigc_canvas import parse_llm_story_candidate_output
from cat_video_generator.infrastructure.ark.gateway import ArkGateway, ArkGatewayError
from cat_video_generator.infrastructure.fake.gateway import FakeArkGateway


class _Responses:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _response(
    text: str | None,
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
) -> SimpleNamespace:
    output = []
    if text is not None:
        output = [
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ]
    return SimpleNamespace(
        id="response-1",
        model="planning-model-returned-by-provider",
        status=status,
        incomplete_details=(
            None
            if incomplete_reason is None
            else SimpleNamespace(reason=incomplete_reason)
        ),
        output=output,
    )


def _gateway(response: Any = None, error: Exception | None = None) -> tuple[ArkGateway, _Responses]:
    responses = _Responses(response, error)
    gateway = object.__new__(ArkGateway)
    gateway._settings = SimpleNamespace(
        ark_planning_model="planning-model",
        ark_structured_output_mode="json_schema",
        ark_director_request_timeout_seconds=37,
    )
    gateway._client = SimpleNamespace(responses=responses)
    return gateway, responses


def _request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _fake_request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def test_ark_creative_text_returns_json_object_and_uses_plain_text_mode() -> None:
    provider_text = json.dumps(
        {"candidates": [{"title": "雨前", "body": "孩子和猫收回画纸。"}]},
        ensure_ascii=False,
    )
    gateway, responses = _gateway(_response(provider_text))

    result = gateway.generate_creative_text(
        prompt="写一组故事候选。",
        output_name="StoryCandidateBatch",
    )

    assert result.payload == {
        "candidates": [{"title": "雨前", "body": "孩子和猫收回画纸。"}]
    }
    assert result.request_hash == _request_hash(
        {
            "input": "生成一个StoryCandidateBatch。",
            "instructions": "写一组故事候选。",
            "mode": "creative_text",
            "model": "planning-model",
            "outputName": "StoryCandidateBatch",
        }
    )
    request = responses.requests[0]
    assert request["model"] == "planning-model"
    assert request["instructions"] == "写一组故事候选。"
    assert request["input"] == "生成一个StoryCandidateBatch。"
    assert request["text"] == {"format": {"type": "text"}}
    assert "json_schema" not in json.dumps(request, ensure_ascii=False)


def test_ark_creative_request_hash_changes_with_instructions_or_input() -> None:
    gateway, _responses = _gateway(_response('{"value":"kept"}'))

    original = gateway.generate_creative_text(
        prompt="创作要求甲。",
        output_name="CreativeOutput",
    )
    changed_instructions = gateway.generate_creative_text(
        prompt="创作要求乙。",
        output_name="CreativeOutput",
    )
    changed_input = gateway.generate_creative_text(
        prompt="创作要求甲。",
        output_name="AlternateCreativeOutput",
    )

    assert len(
        {
            original.request_hash,
            changed_instructions.request_hash,
            changed_input.request_hash,
        }
    ) == 3


def test_ark_creative_text_preserves_non_json_chinese_text() -> None:
    provider_text = "  第一幕：孩子与猫在雨前抢救画纸。\n第二幕：他们笑着回家。  "
    gateway, _responses = _gateway(_response(provider_text))

    result = gateway.generate_creative_text(
        prompt="自由创作故事。",
        output_name="StoryCandidateBatch",
    )

    assert result.payload == provider_text


def test_ark_storyboard_text_preserves_raw_output_and_ordered_image_transport(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "child-cat.png"
    Image.new("RGB", (8, 8), "white").save(image_path)
    raw_text = "镜头一：孩子和猫把画纸收回屋内。"
    gateway, responses = _gateway(_response(raw_text))

    result = gateway.generate_storyboard_text(
        prompt="整理为最小分镜。",
        output_name="CanvasStoryboardPlanOutput",
        image_paths=(image_path,),
    )

    assert result.payload == raw_text
    request = responses.requests[0]
    assert request["text"] == {"format": {"type": "text"}}
    content = request["input"][0]["content"]
    assert content[0] == {
        "type": "input_text",
        "text": "生成一个CanvasStoryboardPlanOutput。",
    }
    assert content[1] == {"type": "input_text", "text": "按顺序查看@图片1"}
    assert content[2]["type"] == "input_image"
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")


def test_ark_creative_text_keeps_json_array_as_raw_text() -> None:
    provider_text = '[{"title":"不能冒充批次","body":"仍由上层诊断"}]'
    gateway, _responses = _gateway(_response(provider_text))

    result = gateway.generate_creative_text(
        prompt="自由创作故事。",
        output_name="StoryCandidateBatch",
    )

    assert result.payload == provider_text


@pytest.mark.parametrize("provider_text", [None, "", "  \n"])
def test_ark_creative_text_rejects_empty_response_with_existing_code(
    provider_text: str | None,
) -> None:
    gateway, _responses = _gateway(_response(provider_text))

    with pytest.raises(ArkGatewayError) as exc_info:
        gateway.generate_creative_text(
            prompt="自由创作故事。",
            output_name="StoryCandidateBatch",
        )

    assert exc_info.value.code == "empty_director_result"
    assert "没有返回文本" in str(exc_info.value)


def test_ark_creative_text_preserves_incomplete_response_semantics() -> None:
    gateway, _responses = _gateway(
        _response(None, status="incomplete", incomplete_reason="max_output_tokens")
    )

    with pytest.raises(ArkGatewayError) as exc_info:
        gateway.generate_creative_text(
            prompt="自由创作故事。",
            output_name="StoryCandidateBatch",
        )

    assert exc_info.value.code == "director_incomplete_max_output_tokens"
    assert exc_info.value.retryable is True


def test_ark_creative_text_preserves_provider_error_semantics() -> None:
    provider_error = ArkAPIConnectionError(
        message="connection lost",
        request=httpx.Request("POST", "https://ark.example.test/responses"),
        request_id="request-provider-1",
    )
    gateway, _responses = _gateway(error=provider_error)

    with pytest.raises(ArkGatewayError) as exc_info:
        gateway.generate_creative_text(
            prompt="自由创作故事。",
            output_name="StoryCandidateBatch",
        )

    assert exc_info.value.code == "ArkAPIConnectionError"
    assert exc_info.value.submission_unknown is True
    assert exc_info.value.request_id == "request-provider-1"


def test_existing_generate_structured_still_rejects_invalid_json() -> None:
    gateway, _responses = _gateway(_response("一段合法的创作文本，但不是 JSON。"))

    with pytest.raises(ArkGatewayError) as exc_info:
        gateway.generate_structured(
            prompt="只返回严格对象。",
            schema={"type": "object"},
            output_name="StrictOutput",
        )

    assert exc_info.value.code == "invalid_director_output"


@pytest.mark.parametrize("provider_text", ["", "  \n"])
def test_existing_generate_structured_keeps_blank_output_compatibility(
    provider_text: str,
) -> None:
    gateway, _responses = _gateway(_response(provider_text))

    with pytest.raises(ArkGatewayError) as exc_info:
        gateway.generate_structured(
            prompt="只返回严格对象。",
            schema={"type": "object"},
            output_name="StrictOutput",
        )

    assert exc_info.value.code == "invalid_director_output"


def test_fake_gateway_story_fixture_is_parseable_and_deterministic() -> None:
    gateway = FakeArkGateway()

    first = gateway.generate_creative_text(
        prompt="写一组孩子与猫的故事候选。",
        output_name="StoryCandidateBatch",
    )
    second = gateway.generate_creative_text(
        prompt="写一组孩子与猫的故事候选。",
        output_name="StoryCandidateBatch",
    )
    parsed = parse_llm_story_candidate_output(first.payload)

    assert isinstance(first, CreativeDirectorResult)
    assert 1 <= len(parsed.batch.candidates) <= 5
    assert all(candidate.title and candidate.body for candidate in parsed.batch.candidates)
    assert second.payload == first.payload
    assert second.response_id == first.response_id
    assert second.model == first.model == "fake-doubao-planner"
    assert second.request_hash == first.request_hash == _fake_request_hash(
        {
            "input": "生成一个StoryCandidateBatch。",
            "instructions": "写一组孩子与猫的故事候选。",
            "mode": "creative_text",
            "model": "fake-doubao-planner",
            "outputName": "StoryCandidateBatch",
        }
    )


def test_fake_gateway_rejects_unknown_creative_fixture_name() -> None:
    gateway = FakeArkGateway()

    with pytest.raises(
        ValueError,
        match="fake provider has no creative fixture for UnknownCreativeOutput",
    ):
        gateway.generate_creative_text(
            prompt="任意创作要求。",
            output_name="UnknownCreativeOutput",
        )
