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
        self.image_calls: list[dict[str, object]] = []

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

    def plan_series(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"seriesBible": {}, "episodes": []},
            response_id="series-response",
            model="planning-model",
            usage={"inputTokens": 800, "outputTokens": 1200},
            request_hash="d" * 64,
        )

    def plan_series_episode(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"title": "准备野餐", "targetDurationSeconds": 12},
            response_id="episode-response",
            model="planning-model",
            usage={"inputTokens": 300, "outputTokens": 500},
            request_hash="e" * 64,
        )

    def analyze_story_source(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return StructuredProviderResult(
            payload={"units": [], "relationSuggestions": []},
            response_id="source-analysis-response",
            model="planning-model",
            usage={"inputTokens": 600, "outputTokens": 700},
            request_hash="f" * 64,
        )

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_paths: tuple[Path, ...],
        reference_roles: tuple[str, ...],
    ) -> ImageProviderResult:
        self.image_calls.append(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "reference_paths": reference_paths,
                "reference_roles": reference_roles,
            }
        )
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
            "negativePrompt": "不得停帧或遗漏最终状态",
            "compiledProviderPrompt": (
                "【生成目标】\n12秒生活短片\n\n"
                "【必须避免】\n不得停帧或遗漏最终状态"
            ),
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
            "prompt": (
                "【生成目标】\n12秒生活短片\n\n"
                "【必须避免】\n不得停帧或遗漏最终状态"
            ),
            "reference_paths": paths,
            "reference_roles": (
                "episode_child",
                "episode_cat",
                "pair_scale",
                "environment",
                "style_board",
            ),
            "reference_video_url": None,
            "duration_seconds": 12,
            "resolution": "480p",
        }
    ]


def test_ark_job_gateway_publishes_an_explicit_previous_episode_video_once(
    tmp_path: Path,
) -> None:
    image_ids = tuple(uuid.uuid4() for _ in range(5))
    image_paths = tuple(tmp_path / f"reference-{index}.png" for index in range(5))
    video_id = uuid.uuid4()
    video_path = tmp_path / "previous.mp4"
    typed = TypedGatewayStub()
    publications: list[tuple[uuid.UUID, uuid.UUID, Path]] = []

    class Publisher:
        def publish_asset(
            self, job_id: uuid.UUID, asset_id: uuid.UUID, path: Path
        ) -> PublishedSegmentReference:
            publications.append((job_id, asset_id, path))
            return PublishedSegmentReference(
                publication_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
                url="https://media.example.test/previous.mp4?X-Amz-Signature=secret",
            )

    def resolve(ids: tuple[uuid.UUID, ...]) -> tuple[Path, ...]:
        if ids == image_ids:
            return image_paths
        if ids == (video_id,):
            return (video_path,)
        return ()

    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=resolve,
        extract_video_frames=lambda _asset_id, _seconds: (),
        publish_segment_reference=Publisher(),
    )
    job_id = uuid.uuid4()
    frozen_input: dict[str, object] = {
        "prompt": "承接上一集结尾开始野餐",
        "negativePrompt": "不得复制上一集的动作节奏",
        "compiledProviderPrompt": (
            "【生成目标】\n承接上一集结尾开始野餐\n\n"
            "【必须避免】\n不得复制上一集的动作节奏"
        ),
        "referenceAssetIds": [str(item) for item in image_ids],
        "referenceRoles": [
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        ],
        "previousEpisodeVideoAssetId": str(video_id),
        "durationSeconds": 12,
        "resolution": "480p",
    }

    gateway.prepare_submission(job_id=job_id, kind="generate_video", frozen_input=frozen_input)
    submission = gateway.submit(
        job_id=job_id, kind="generate_video", frozen_input=frozen_input
    )

    assert publications == [(job_id, video_id, video_path)]
    assert typed.video_calls[0]["reference_video_url"].startswith("https://")
    assert submission.metadata == {
        "publicationId": "22222222-2222-4222-8222-222222222222"
    }


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


def test_ark_job_gateway_routes_each_series_planning_kind_once() -> None:
    gateway = ArkProviderJobGateway(
        TypedGatewayStub(),
        resolve_asset_paths=lambda _ids: (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    series = gateway.submit(
        job_id=uuid.uuid4(),
        kind="plan_series",
        frozen_input={"prompt": "规划三集", "outputSchema": {"type": "object"}},
    )
    episode = gateway.submit(
        job_id=uuid.uuid4(),
        kind="plan_series_episode",
        frozen_input={"prompt": "扩写第一集", "outputSchema": {"type": "object"}},
    )
    source = gateway.submit(
        job_id=uuid.uuid4(),
        kind="analyze_story_source",
        frozen_input={"prompt": "分析导入文本", "outputSchema": {"type": "object"}},
    )

    assert series.result is not None and series.result["responseId"] == "series-response"
    assert episode.result is not None and episode.result["responseId"] == "episode-response"
    assert source.result is not None and source.result["responseId"] == "source-analysis-response"


def test_ark_job_gateway_preserves_image_usage_and_request_id() -> None:
    typed = TypedGatewayStub()
    reference_ids = tuple(uuid.uuid4() for _ in range(3))
    reference_paths = tuple(Path(f"reference-{index}.png") for index in range(3))
    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda ids: reference_paths if ids == reference_ids else (),
        extract_video_frames=lambda _asset_id, _seconds: (),
    )

    submission = gateway.submit(
        job_id=uuid.uuid4(),
        kind="generate_image",
        frozen_input={
            "prompt": "生成雨天玄关空场景",
            "negativePrompt": "不得出现儿童、猫咪或其他动物",
            "referenceAssetIds": [str(item) for item in reference_ids],
            "referenceRoles": ["style_board", "episode_child", "episode_cat"],
        },
    )

    assert submission.usage == {"generatedImages": 1, "totalTokens": 320}
    assert submission.result is not None
    assert submission.result["responseId"] == "image-response"
    assert typed.image_calls == [
        {
            "prompt": "生成雨天玄关空场景",
            "negative_prompt": "不得出现儿童、猫咪或其他动物",
            "reference_paths": reference_paths,
            "reference_roles": ("style_board", "episode_child", "episode_cat"),
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
