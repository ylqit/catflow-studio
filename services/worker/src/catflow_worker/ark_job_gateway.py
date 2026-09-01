from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from catflow.application.gateways import (
    DiagnosticGateway,
    ImageGenerationGateway,
    PlanningGateway,
    StructuredProviderResult,
    VideoGenerationGateway,
)

from .runner import ProviderPoll, ProviderSubmission


class ArkTypedProvider(
    PlanningGateway,
    ImageGenerationGateway,
    DiagnosticGateway,
    VideoGenerationGateway,
    Protocol,
):
    pass


class ArkProviderJobGateway:
    """Compile frozen CatFlow jobs into typed Ark calls and normalize their results."""

    def __init__(
        self,
        gateway: ArkTypedProvider,
        *,
        resolve_asset_paths: Callable[[tuple[uuid.UUID, ...]], tuple[Path, ...]],
        extract_video_frames: Callable[[uuid.UUID, tuple[float, ...]], tuple[Path, ...]],
    ) -> None:
        self._gateway = gateway
        self._resolve_asset_paths = resolve_asset_paths
        self._extract_video_frames = extract_video_frames

    def submit(
        self,
        *,
        job_id: uuid.UUID,
        kind: str,
        frozen_input: dict[str, object],
    ) -> ProviderSubmission:
        if kind == "plan_story":
            result = self._gateway.plan_story(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "generate_image":
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            result = self._gateway.generate_image(
                prompt=_required_string(frozen_input, "prompt"),
                reference_paths=self._resolve_asset_paths(reference_ids),
            )
            return ProviderSubmission(
                result={
                    "url": result.url,
                    "responseId": result.response_id,
                    "model": result.model,
                }
            )
        if kind == "diagnose_image":
            candidate_id = uuid.UUID(_required_string(frozen_input, "candidateAssetId"))
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            paths = self._resolve_asset_paths((candidate_id, *reference_ids))
            result = self._gateway.diagnose(
                prompt=_required_string(frozen_input, "prompt"),
                image_paths=paths,
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "generate_video":
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            reference_roles = tuple(
                str(item) for item in frozen_input.get("referenceRoles", [])  # type: ignore[union-attr]
            )
            result = self._gateway.submit_video(
                prompt=_required_string(frozen_input, "prompt"),
                reference_paths=self._resolve_asset_paths(reference_ids),
                reference_roles=reference_roles,
                duration_seconds=int(frozen_input.get("durationSeconds", 12)),
                resolution=_required_string(frozen_input, "resolution"),
            )
            return ProviderSubmission(
                taskId=result.task_id,
                metadata={"requestId": result.request_id} if result.request_id else None,
            )
        if kind == "diagnose_video":
            video_asset_id = uuid.UUID(_required_string(frozen_input, "videoAssetId"))
            timestamps = tuple(
                float(item) for item in frozen_input.get("timestampsSeconds", [])  # type: ignore[union-attr]
            )
            frame_paths = self._extract_video_frames(video_asset_id, timestamps)
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            result = self._gateway.diagnose(
                prompt=_required_string(frozen_input, "prompt"),
                image_paths=(*self._resolve_asset_paths(reference_ids), *frame_paths),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        raise ValueError(f"Ark does not own CatFlow job kind: {kind}")

    def poll(self, provider_task_id: str) -> ProviderPoll:
        result = self._gateway.poll_video(provider_task_id)
        if result.status == "running":
            return ProviderPoll(status="running")
        if result.status == "failed":
            return ProviderPoll(status="failed", error=result.error)
        return ProviderPoll(
            status="succeeded",
            result={
                "videoUrl": result.video_url,
                "lastFrameUrl": result.last_frame_url,
                "model": result.model,
                "durationSeconds": result.duration_seconds,
                "ratio": result.ratio,
                "resolution": result.resolution,
            },
        )

    def cancel(self, provider_task_id: str) -> bool:
        return self._gateway.cancel_video(provider_task_id)


def _structured_submission(result: StructuredProviderResult) -> ProviderSubmission:
    return ProviderSubmission(
        result={
            "payload": result.payload,
            "responseId": result.response_id,
            "model": result.model,
            "requestHash": result.request_hash,
        },
        usage=result.usage,
    )


def _required_string(document: dict[str, object], key: str) -> str:
    value = str(document.get(key, "")).strip()
    if not value:
        raise ValueError(f"frozen Ark input requires {key}")
    return value


def _required_dict(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"frozen Ark input requires object {key}")
    return value


def _uuid_tuple(value: object) -> tuple[uuid.UUID, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("frozen Ark asset IDs must be an array")
    return tuple(uuid.UUID(str(item)) for item in value)
