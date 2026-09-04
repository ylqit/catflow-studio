from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from catflow.application.gateways import ProviderGatewayError, SegmentVideoGenerationRequest
from catflow_worker.ark_gateway import ArkGatewaySettings, ArkTypedGateway


class Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.result

    def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.result

    def get(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.result

    def delete(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _settings() -> ArkGatewaySettings:
    return ArkGatewaySettings(
        api_key="server-only-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        planning_model="planning-model",
        image_model="image-model",
        video_model="video-model",
        diagnostic_model="diagnostic-model",
        request_timeout_seconds=30,
    )


def _response(payload: dict[str, object]) -> object:
    return SimpleNamespace(
        id="response-1",
        model="planning-model",
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=json.dumps(payload))],
            )
        ],
        usage=SimpleNamespace(input_tokens=120, output_tokens=80, total_tokens=200),
    )


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (32, 48), color).save(path, format="PNG")
    return path


def test_ark_planning_gateway_uses_supported_json_object_mode_and_typed_metadata() -> None:
    responses = Recorder(_response({"title": "雨天擦爪", "targetDurationSeconds": 12}))
    client = SimpleNamespace(responses=responses)
    gateway = ArkTypedGateway(_settings(), client=client)

    result = gateway.plan_story(
        prompt="生成一个12秒生活微事件",
        output_schema={"type": "object", "required": ["title"]},
    )

    assert result.payload["title"] == "雨天擦爪"
    assert result.response_id == "response-1"
    assert result.usage == {"inputTokens": 120, "outputTokens": 80, "totalTokens": 200}
    request = responses.calls[0]
    assert request["model"] == "planning-model"
    assert request["store"] is False
    assert request["text"] == {"format": {"type": "json_object"}}
    assert "json_schema" not in json.dumps(request, ensure_ascii=False)


def test_director_planner_gets_a_larger_output_budget_without_changing_story_budget() -> None:
    responses = Recorder(_response({"targetDurationSeconds": 12, "shots": []}))
    gateway = ArkTypedGateway(_settings(), client=SimpleNamespace(responses=responses))

    gateway.plan_story(prompt="生活故事", output_schema={"type": "object"})
    gateway.plan_shots(prompt="专业分镜", output_schema={"type": "object"})

    assert responses.calls[0]["max_output_tokens"] == 4000
    assert responses.calls[1]["max_output_tokens"] == 8000


def test_incomplete_structured_response_preserves_reason_usage_and_response_id() -> None:
    response = SimpleNamespace(
        id="response-incomplete-1",
        model="planning-model",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=SimpleNamespace(input_tokens=321, output_tokens=8000, total_tokens=8321),
    )
    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(responses=Recorder(response))
    )

    with pytest.raises(ProviderGatewayError) as captured:
        gateway.plan_shots(prompt="专业分镜", output_schema={"type": "object"})

    assert captured.value.incomplete_reason == "max_output_tokens"
    assert captured.value.provider_status == "incomplete"
    assert captured.value.max_output_tokens == 8000
    assert captured.value.usage == {
        "inputTokens": 321,
        "outputTokens": 8000,
        "totalTokens": 8321,
    }
    assert captured.value.request_id == "response-incomplete-1"
    assert captured.value.as_error_document()["incompleteReason"] == "max_output_tokens"


def test_structured_response_rejects_unsafe_nesting_before_business_validation() -> None:
    nested: dict[str, object] = {"value": "leaf"}
    for _ in range(45):
        nested = {"next": nested}
    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(responses=Recorder(_response(nested)))
    )

    with pytest.raises(ProviderGatewayError) as captured:
        gateway.plan_shots(prompt="专业分镜", output_schema={"type": "object"})

    assert captured.value.code == "structured_output_too_deep"


def test_ark_image_and_video_gateways_preserve_five_reference_order(
    tmp_path: Path,
) -> None:
    image_recorder = Recorder(
        SimpleNamespace(
            data=[SimpleNamespace(url="https://ark.example/result.png")],
            model="image-model",
            id="image-response-1",
            usage=SimpleNamespace(
                generated_images=1,
                output_tokens=320,
                total_tokens=320,
            ),
        )
    )
    video_tasks = Recorder(SimpleNamespace(id="video-task-1"))
    client = SimpleNamespace(
        images=SimpleNamespace(generate=image_recorder.generate),
        content_generation=SimpleNamespace(tasks=video_tasks),
    )
    gateway = ArkTypedGateway(_settings(), client=client)
    references = tuple(
        _image(tmp_path / f"reference-{index}.png", color)
        for index, color in enumerate(("red", "green", "blue", "white", "gray"), 1)
    )

    image_result = gateway.generate_image(
        prompt="生成雨天玄关空场景",
        negative_prompt="不得出现儿童、猫咪或其他动物",
        reference_paths=references[:3],
        reference_roles=("style_board", "episode_child", "episode_cat"),
    )
    video_result = gateway.submit_video(
        prompt="12秒一人一猫生活短片",
        reference_paths=references,
        reference_roles=(
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        ),
        duration_seconds=12,
        resolution="480p",
    )

    assert image_result.url == "https://ark.example/result.png"
    assert image_result.usage == {
        "generatedImages": 1,
        "outputTokens": 320,
        "totalTokens": 320,
    }
    assert video_result.task_id == "video-task-1"
    image_request = image_recorder.calls[0]
    assert image_request["watermark"] is False
    assert image_request["response_format"] == "url"
    assert image_request["optimize_prompt"] is False
    assert image_request["prompt"] == (
        "【生成目标】\n生成雨天玄关空场景\n\n"
        "【必须避免】\n不得出现儿童、猫咪或其他动物"
    )
    assert len(image_request["image"]) == 3
    video_request = video_tasks.calls[0]
    assert video_request["duration"] == 12
    assert video_request["resolution"] == "480p"
    assert video_request["ratio"] == "9:16"
    image_content = [
        item for item in video_request["content"] if item["type"] == "image_url"  # type: ignore[index]
    ]
    text_content = [
        item for item in video_request["content"] if item["type"] == "text"  # type: ignore[index]
    ]
    assert len(text_content) == 1
    assert text_content[0]["text"].endswith(
        "参考图片按顺序承担以下职责：episode_child → episode_cat → pair_scale → "
        "environment → style_board。"
    )
    assert len(image_content) == 5
    assert all(item["role"] == "reference_image" for item in image_content)


def test_ark_video_gateway_places_an_explicit_https_video_after_fixed_images(
    tmp_path: Path,
) -> None:
    video_tasks = Recorder(SimpleNamespace(id="video-task-2"))
    gateway = ArkTypedGateway(
        _settings(),
        client=SimpleNamespace(
            content_generation=SimpleNamespace(tasks=video_tasks),
        ),
    )
    references = tuple(
        _image(tmp_path / f"reference-{index}.png", color)
        for index, color in enumerate(("red", "green", "blue", "white", "gray"), 1)
    )

    gateway.submit_video(
        prompt="承接上一集开场",
        reference_paths=references,
        reference_roles=(
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        ),
        reference_video_url="https://media.example.test/episode-1.mp4?signature=safe",
        duration_seconds=12,
        resolution="480p",
    )

    content = video_tasks.calls[0]["content"]
    assert [item["type"] for item in content] == [
        "text",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
        "video_url",
    ]
    assert content[-1] == {
        "type": "video_url",
        "video_url": {
            "url": "https://media.example.test/episode-1.mp4?signature=safe"
        },
        "role": "reference_video",
    }
    assert "不得取代前五张图片" in content[0]["text"]


def test_ark_video_gateway_rejects_non_https_video_references(tmp_path: Path) -> None:
    gateway = ArkTypedGateway(
        _settings(),
        client=SimpleNamespace(
            content_generation=SimpleNamespace(tasks=Recorder(SimpleNamespace(id="unused"))),
        ),
    )

    with pytest.raises(ValueError, match="HTTPS"):
        gateway.submit_video(
            prompt="不应提交",
            reference_paths=(),
            reference_roles=(),
            reference_video_url="file:///private/episode.mp4",
            duration_seconds=12,
            resolution="480p",
        )


def test_ark_video_poll_maps_succeeded_media_urls() -> None:
    task = SimpleNamespace(
        id="video-task-1",
        status="succeeded",
        content=SimpleNamespace(
            video_url="https://ark.example/video.mp4",
            last_frame_url="https://ark.example/tail.png",
        ),
        error=None,
        model="video-model",
        duration=12,
        ratio="9:16",
        resolution="480p",
        usage=SimpleNamespace(completion_tokens=9600, total_tokens=9600),
    )
    tasks = Recorder(task)
    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    )

    result = gateway.poll_video("video-task-1")

    assert result.status == "succeeded"
    assert result.video_url == "https://ark.example/video.mp4"
    assert result.duration_seconds == 12
    assert result.usage == {"completionTokens": 9600, "totalTokens": 9600}
    assert "inputTokens" not in result.usage


def test_ark_usage_does_not_invent_zero_values_for_missing_provider_fields() -> None:
    responses = Recorder(
        SimpleNamespace(
            id="response-usage",
            model="planning-model",
            status="completed",
            output_text=json.dumps({"title": "擦爪"}),
            usage=SimpleNamespace(output_tokens=42),
        )
    )
    gateway = ArkTypedGateway(_settings(), client=SimpleNamespace(responses=responses))

    result = gateway.plan_story(prompt="生成提案", output_schema={"type": "object"})

    assert result.usage == {"outputTokens": 42}


def test_ark_segment_gateway_submits_one_video_and_seven_ordered_images(
    tmp_path: Path,
) -> None:
    tasks = Recorder(SimpleNamespace(id="segment-task-1", request_id="request-1"))
    gateway = ArkTypedGateway(
        _settings(),
        client=SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks)),
    )
    images = tuple(
        _image(tmp_path / f"segment-{index}.png", color)
        for index, color in enumerate(
            ("black", "white", "red", "green", "blue", "gray", "yellow"), 1
        )
    )

    result = gateway.submit_segment_video(
        SegmentVideoGenerationRequest(
            instruction="只修复擦爪动作。",
            prompt="只修复擦爪动作并锁定首尾衔接。",
            negative_prompt="禁止身份漂移和接缝双影。",
            context_video_url=(
                "https://media.example.test/catflow/context.mp4?signature=temporary"
            ),
            issue_start_seconds=4.0,
            issue_end_seconds=8.0,
            anchor_in_path=images[0],
            anchor_out_path=images[1],
            canon_reference_paths=images[2:],
            canon_reference_roles=(
                "episode_child",
                "episode_cat",
                "pair_scale",
                "environment",
                "style_board",
            ),
            duration_seconds=6,
            resolution="480p",
            ratio="9:16",
        )
    )

    assert result.task_id == "segment-task-1"
    request = tasks.calls[0]
    content = request["content"]
    assert [item["type"] for item in content] == [  # type: ignore[index]
        "text",
        "video_url",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
        "image_url",
    ]
    assert content[1]["role"] == "reference_video"  # type: ignore[index]
    assert content[1]["video_url"]["url"] == (  # type: ignore[index]
        "https://media.example.test/catflow/context.mp4?signature=temporary"
    )
    assert [item["role"] for item in content[2:]] == [  # type: ignore[index]
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_image",
        "reference_image",
    ]
    assert request["duration"] == 6
    assert request["generate_audio"] is False
    assert request["watermark"] is False
    assert "本区间修改目标" in content[0]["text"]  # type: ignore[index]
    assert "编辑类型" not in content[0]["text"]  # type: ignore[index]


def test_ark_segment_request_rejects_a_non_https_reference_url_before_submission(
    tmp_path: Path,
) -> None:
    tasks = Recorder(SimpleNamespace(id="must-not-be-created"))
    gateway = ArkTypedGateway(
        _settings(),
        client=SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks)),
    )
    images = tuple(_image(tmp_path / f"blocked-{index}.png", "gray") for index in range(7))

    with pytest.raises(ValueError, match="HTTPS"):
        gateway.submit_segment_video(
            SegmentVideoGenerationRequest(
                instruction="只修复擦爪动作。",
                prompt="只修复擦爪动作。",
                negative_prompt="禁止身份漂移。",
                context_video_url="http://127.0.0.1/context.mp4",
                issue_start_seconds=4.0,
                issue_end_seconds=8.0,
                anchor_in_path=images[0],
                anchor_out_path=images[1],
                canon_reference_paths=images[2:],
                canon_reference_roles=(
                    "episode_child",
                    "episode_cat",
                    "pair_scale",
                    "environment",
                    "style_board",
                ),
                duration_seconds=4,
                resolution="480p",
                ratio="9:16",
            )
        )

    assert tasks.calls == []


def test_ark_submission_timeout_is_never_presented_as_safe_to_retry() -> None:
    class TimeoutResponses:
        def create(self, **_kwargs: object) -> object:
            raise TimeoutError("timed out after sending")

    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(responses=TimeoutResponses())
    )

    with pytest.raises(ProviderGatewayError) as captured:
        gateway.plan_story(prompt="雨天擦爪", output_schema={"type": "object"})

    assert captured.value.submission_unknown is True
    assert captured.value.retryable is False
    assert captured.value.timed_out is True
