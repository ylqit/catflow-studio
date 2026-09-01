from __future__ import annotations

import uuid
from pathlib import Path

from catflow.application.gateways import (
    ImageProviderResult,
    StructuredProviderResult,
    VideoPollResult,
    VideoSubmissionResult,
)
from catflow_worker.ark_job_gateway import ArkProviderJobGateway


class TypedGatewayStub:
    def __init__(self) -> None:
        self.video_calls: list[dict[str, object]] = []

    def plan_story(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"title": "雨天擦爪", "targetDurationSeconds": 12},
            response_id="planning-response",
            model="planning-model",
            usage={"totalTokens": 200},
            request_hash="a" * 64,
        )

    def generate_image(
        self, *, prompt: str, reference_paths: tuple[Path, ...]
    ) -> ImageProviderResult:
        return ImageProviderResult(
            url="https://ark.example/environment.png",
            response_id="image-response",
            model="image-model",
        )

    def diagnose(
        self,
        *,
        prompt: str,
        image_paths: tuple[Path, ...],
        output_schema: dict[str, object],
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"style": "pass", "warnings": []},
            response_id="diagnosis-response",
            model="diagnostic-model",
            usage={"totalTokens": 90},
            request_hash="b" * 64,
        )

    def submit_video(self, **kwargs: object) -> VideoSubmissionResult:
        self.video_calls.append(kwargs)
        return VideoSubmissionResult(task_id="video-task-1")

    def poll_video(self, task_id: str) -> VideoPollResult:
        return VideoPollResult(
            status="succeeded",
            video_url="https://ark.example/video.mp4",
            duration_seconds=12,
            ratio="9:16",
            resolution="480p",
        )

    def cancel_video(self, task_id: str) -> bool:
        return True


def test_ark_job_gateway_persists_structured_planning_result_as_immediate_payload(
    tmp_path: Path,
) -> None:
    typed = TypedGatewayStub()
    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda _ids: (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    submission = gateway.submit(
        job_id=uuid.uuid4(),
        kind="plan_story",
        frozen_input={
            "prompt": "12秒雨天擦爪",
            "outputSchema": {"type": "object"},
        },
    )

    assert submission.task_id is None
    assert submission.result == {
        "payload": {"title": "雨天擦爪", "targetDurationSeconds": 12},
        "responseId": "planning-response",
        "model": "planning-model",
        "requestHash": "a" * 64,
    }
    assert submission.usage == {"totalTokens": 200}


def test_ark_job_gateway_submits_video_with_frozen_five_reference_order(
    tmp_path: Path,
) -> None:
    asset_ids = tuple(uuid.uuid4() for _ in range(5))
    paths = tuple(tmp_path / f"reference-{index}.png" for index in range(5))
    typed = TypedGatewayStub()
    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda requested: paths if requested == asset_ids else (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    submission = gateway.submit(
        job_id=uuid.uuid4(),
        kind="generate_video",
        frozen_input={
            "prompt": "12秒生活短片",
            "referenceAssetIds": [str(item) for item in asset_ids],
            "referenceRoles": [
                "episode_child",
                "episode_cat",
                "pair_scale",
                "environment",
                "style_board",
            ],
            "durationSeconds": 12,
            "resolution": "480p",
        },
    )

    assert submission.task_id == "video-task-1"
    assert typed.video_calls == [
        {
            "prompt": "12秒生活短片",
            "reference_paths": paths,
            "reference_roles": (
                "episode_child",
                "episode_cat",
                "pair_scale",
                "environment",
                "style_board",
            ),
            "duration_seconds": 12,
            "resolution": "480p",
        }
    ]


def test_ark_job_gateway_poll_returns_downloadable_provider_result() -> None:
    gateway = ArkProviderJobGateway(
        TypedGatewayStub(),
        resolve_asset_paths=lambda _ids: (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    poll = gateway.poll("video-task-1")

    assert poll.status == "succeeded"
    assert poll.result == {
        "videoUrl": "https://ark.example/video.mp4",
        "lastFrameUrl": None,
        "model": None,
        "durationSeconds": 12,
        "ratio": "9:16",
        "resolution": "480p",
    }
