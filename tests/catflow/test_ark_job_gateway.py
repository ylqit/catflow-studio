from __future__ import annotations

import uuid
from pathlib import Path

from catflow.application.gateways import (
    ImageProviderResult,
    SegmentVideoGenerationRequest,
    StructuredProviderResult,
    VideoPollResult,
    VideoSubmissionResult,
)
from catflow_worker.ark_job_gateway import ArkProviderJobGateway
from catflow_worker.segment_publisher import PublishedSegmentReference


class TypedGatewayStub:
    def __init__(self) -> None:
        self.video_calls: list[dict[str, object]] = []
        self.segment_video_calls: list[SegmentVideoGenerationRequest] = []

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

    def plan_shots(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"targetDurationSeconds": 12, "directorTreatment": {}, "shots": []},
            response_id="director-response",
            model="planning-model",
            usage={"inputTokens": 600, "outputTokens": 900},
            request_hash="c" * 64,
        )

    def generate_image(
        self, *, prompt: str, reference_paths: tuple[Path, ...]
    ) -> ImageProviderResult:
        return ImageProviderResult(
            url="https://ark.example/environment.png",
            response_id="image-response",
            model="image-model",
            usage={"generatedImages": 1, "totalTokens": 320},
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

    def submit_segment_video(self, request: SegmentVideoGenerationRequest) -> VideoSubmissionResult:
        self.segment_video_calls.append(request)
        return VideoSubmissionResult(task_id="segment-task-1", request_id="segment-request-1")

    def poll_video(self, task_id: str) -> VideoPollResult:
        return VideoPollResult(
            status="succeeded",
            video_url="https://ark.example/video.mp4",
            duration_seconds=12,
            ratio="9:16",
            resolution="480p",
            usage={"completionTokens": 9600, "totalTokens": 9600},
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


def test_ark_job_gateway_uses_the_professional_director_planning_boundary() -> None:
    gateway = ArkProviderJobGateway(
        TypedGatewayStub(),
        resolve_asset_paths=lambda _ids: (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    submission = gateway.submit(
        job_id=uuid.uuid4(),
        kind="plan_shots",
        frozen_input={
            "prompt": "生成专业导演执行单",
            "outputSchema": {"type": "object"},
        },
    )

    assert submission.result is not None
    assert submission.result["responseId"] == "director-response"
    assert submission.usage == {"inputTokens": 600, "outputTokens": 900}


def test_ark_job_gateway_preserves_image_usage_and_request_id() -> None:
    gateway = ArkProviderJobGateway(
        TypedGatewayStub(),
        resolve_asset_paths=lambda _ids: (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    submission = gateway.submit(
        job_id=uuid.uuid4(),
        kind="generate_image",
        frozen_input={"prompt": "共享环境", "referenceAssetIds": []},
    )

    assert submission.usage == {"generatedImages": 1, "totalTokens": 320}
    assert submission.result is not None
    assert submission.result["responseId"] == "image-response"


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
    assert poll.usage == {"completionTokens": 9600, "totalTokens": 9600}


def test_ark_job_gateway_prepares_and_submits_frozen_segment_repair(
    tmp_path: Path,
) -> None:
    base_id = uuid.uuid4()
    canon_ids = tuple(uuid.uuid4() for _ in range(5))
    canon_paths = tuple(tmp_path / f"canon-{index}.png" for index in range(5))
    context = tmp_path / "context.mp4"
    anchor_in = tmp_path / "anchor-in.png"
    anchor_out = tmp_path / "anchor-out.png"
    typed = TypedGatewayStub()
    prepared: list[tuple[object, ...]] = []
    published: list[tuple[uuid.UUID, Path]] = []

    def prepare(*args: object) -> tuple[Path, Path, Path]:
        prepared.append(args)
        return context, anchor_in, anchor_out

    class Publisher:
        def publish(self, job_id: uuid.UUID, path: Path) -> PublishedSegmentReference:
            published.append((job_id, path))
            return PublishedSegmentReference(
                publication_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
                url="https://media.example.test/context.mp4?X-Amz-Signature=secret",
            )

    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda requested: canon_paths if requested == canon_ids else (),
        extract_video_frames=lambda _asset_id, _seconds: (),
        prepare_segment_media=prepare,
        publish_segment_reference=Publisher(),
    )
    job_id = uuid.uuid4()

    gateway.prepare_submission(
        job_id=job_id,
        kind="regenerate_video_segment",
        frozen_input={
            "baseVideoAssetId": str(base_id),
            "issueRange": {"startFrame": 96, "endFrame": 192},
            "generationRange": {"startFrame": 72, "endFrame": 216},
            "providerDurationSeconds": 6,
        },
    )
    submission = gateway.submit(
        job_id=job_id,
        kind="regenerate_video_segment",
        frozen_input={
            "baseVideoAssetId": str(base_id),
            "issueRange": {"startFrame": 96, "endFrame": 192},
            "generationRange": {"startFrame": 72, "endFrame": 216},
            "instruction": "让孩子逐只擦干猫爪。",
            "prompt": "只修复擦爪动作。",
            "negativePrompt": "禁止身份漂移。",
            "referenceAssetIds": [str(item) for item in canon_ids],
            "referenceRoles": [
                "anchor_in",
                "anchor_out",
                "episode_child",
                "episode_cat",
                "pair_scale",
                "environment",
                "style_board",
            ],
            "providerDurationSeconds": 6,
            "resolution": "480p",
            "aspectRatio": "9:16",
        },
    )

    assert submission.task_id == "segment-task-1"
    assert submission.metadata == {
        "requestId": "segment-request-1",
        "publicationId": "11111111-1111-4111-8111-111111111111",
    }
    assert prepared == [(job_id, base_id, 72, 216, 96, 192, 6)]
    assert published == [(job_id, context)]
    request = typed.segment_video_calls[0]
    assert request.context_video_url == (
        "https://media.example.test/context.mp4?X-Amz-Signature=secret"
    )
    assert request.instruction == "让孩子逐只擦干猫爪。"
    assert request.issue_start_seconds == 4.0
    assert request.issue_end_seconds == 8.0
    assert request.anchor_in_path == anchor_in
    assert request.anchor_out_path == anchor_out
    assert request.canon_reference_paths == canon_paths
    assert request.canon_reference_roles == (
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    )
    assert request.duration_seconds == 6
