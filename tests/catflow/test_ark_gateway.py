from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from catflow.application.gateways import ProviderGatewayError
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


def test_ark_image_and_video_gateways_preserve_five_reference_order(
    tmp_path: Path,
) -> None:
    image_recorder = Recorder(
        SimpleNamespace(
            data=[SimpleNamespace(url="https://ark.example/result.png")],
            model="image-model",
            id="image-response-1",
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

    image_result = gateway.generate_image(prompt="共享环境", reference_paths=references[:1])
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
    assert video_result.task_id == "video-task-1"
    image_request = image_recorder.calls[0]
    assert image_request["watermark"] is False
    assert image_request["response_format"] == "url"
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
    )
    tasks = Recorder(task)
    gateway = ArkTypedGateway(
        _settings(), client=SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    )

    result = gateway.poll_video("video-task-1")

    assert result.status == "succeeded"
    assert result.video_url == "https://ark.example/video.mp4"
    assert result.duration_seconds == 12


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
