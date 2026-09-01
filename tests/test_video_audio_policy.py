from __future__ import annotations

from types import SimpleNamespace

from cat_video_generator.domain.rendering import AudioPolicy, RenderOperation, VideoInputPlan
from cat_video_generator.infrastructure.ark.gateway import ArkGateway, _video_task_result


def test_video_input_plan_defaults_to_required_native_audio() -> None:
    plan = VideoInputPlan(
        operation=RenderOperation.SHOT,
        resolution="720p",
        duration_seconds=8,
        bindings=[],
    )

    assert plan.audio_policy is AudioPolicy.NATIVE_REQUIRED
    assert plan.model_dump(mode="json", by_alias=True)["audioPolicy"] == "native_required"


def test_ark_submission_maps_explicit_audio_policy_to_provider_flag() -> None:
    calls: list[dict[str, object]] = []

    class Tasks:
        def create(self, **values: object) -> object:
            calls.append(values)
            return SimpleNamespace(id="task-1")

    gateway = object.__new__(ArkGateway)
    gateway._settings = SimpleNamespace(  # type: ignore[attr-defined]
        ark_video_model="seedance-test",
        ark_video_api_timeout_seconds=30,
    )
    gateway._client = SimpleNamespace(  # type: ignore[attr-defined]
        content_generation=SimpleNamespace(tasks=Tasks())
    )

    for policy in (AudioPolicy.NATIVE_REQUIRED, AudioPolicy.NONE):
        gateway.submit_video(
            prompt="缓慢抬头",
            input_plan=VideoInputPlan(
                operation=RenderOperation.SHOT,
                resolution="720p",
                duration_seconds=8,
                audioPolicy=policy,
                bindings=[],
            ),
            input_sources=(),
        )

    assert [item["generate_audio"] for item in calls] == [True, False]
    assert [item["return_last_frame"] for item in calls] == [True, True]


def test_ark_task_result_preserves_provider_returned_tail_frame_url() -> None:
    result = _video_task_result(
        SimpleNamespace(
            id="task-1",
            status="succeeded",
            content=SimpleNamespace(
                video_url="https://provider.example/video.mp4",
                last_frame_url="https://provider.example/tail.png",
            ),
            error=None,
            created_at=None,
            duration="8",
            model="seedance-test",
            ratio="9:16",
            resolution="720p",
            generate_audio=True,
        )
    )

    assert result.video_url == "https://provider.example/video.mp4"
    assert result.last_frame_url == "https://provider.example/tail.png"
