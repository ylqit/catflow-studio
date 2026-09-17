from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from catflow.application.gateways import (
    DiagnosticGateway,
    ImageGenerationGateway,
    PlanningGateway,
    ProviderGatewayError,
    SegmentVideoGenerationRequest,
    StructuredProviderResult,
    VideoGenerationGateway,
)
from catflow.application.video_edit import CANON_ROLES
from catflow.application.video_generation import compile_provider_video_prompt
from catflow.infrastructure.object_storage import ObjectPublisherError

from .runner import ProviderPoll, ProviderSubmission
from .segment_publisher import PublishedSegmentReference, SegmentReferencePublisher


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
        prepare_segment_media: Callable[
            [uuid.UUID, uuid.UUID, int, int, int, int, int],
            tuple[Path, Path, Path],
        ]
        | None = None,
        publish_segment_reference: SegmentReferencePublisher | None = None,
        prepare_edit_plan_frames: Callable[[uuid.UUID, dict[str, object]], tuple[Path, ...]]
        | None = None,
    ) -> None:
        self._gateway = gateway
        self._resolve_asset_paths = resolve_asset_paths
        self._extract_video_frames = extract_video_frames
        self._prepare_segment_media = prepare_segment_media
        self._prepare_edit_plan_frames = prepare_edit_plan_frames
        self._video_reference_publisher = publish_segment_reference
        self._prepared_edit_plan_frames: dict[uuid.UUID, tuple[Path, ...]] = {}
        self._prepared_video_references: dict[uuid.UUID, PublishedSegmentReference] = {}
        self._prepared_segment_media: dict[
            uuid.UUID,
            tuple[Path | None, Path | None, Path | None, PublishedSegmentReference | None],
        ] = {}

    def prepare_submission(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> None:
        contract = frozen_input.get("executionContract", {})
        if contract.get("apiBaseUrl"):
            self._gateway.validate_execution_contract(contract, kind)
        if kind == "plan_video_edit":
            if self._prepare_edit_plan_frames is None:
                raise ValueError("edit plan timeline frame extraction is not configured")
            self._prepared_edit_plan_frames[job_id] = self._prepare_edit_plan_frames(
                job_id, frozen_input
            )
            return
        if kind == "generate_video":
            asset_value = frozen_input.get("previousEpisodeVideoAssetId")
            if asset_value is None:
                return
            if self._video_reference_publisher is None:
                raise ProviderGatewayError(
                    code="video_reference_publisher_unavailable",
                    message="Ark video continuity reference requires a configured HTTPS publisher",
                    retryable=False,
                    submission_unknown=False,
                )
            asset_id = uuid.UUID(str(asset_value))
            paths = self._resolve_asset_paths((asset_id,))
            if len(paths) != 1:
                raise ValueError("previous episode video must resolve to exactly one durable file")
            try:
                self._prepared_video_references[job_id] = (
                    self._video_reference_publisher.publish_asset(job_id, asset_id, paths[0])
                )
            except ObjectPublisherError as exc:
                raise ProviderGatewayError(
                    code=exc.code,
                    message=exc.message,
                    retryable=False,
                    submission_unknown=False,
                ) from exc
            return
        if kind != "regenerate_video_segment":
            return
        from_frame = frozen_input.get("generationMode") == "from_frame"
        if not from_frame and self._video_reference_publisher is None:
            raise ProviderGatewayError(
                code="segment_reference_publisher_unavailable",
                message="Ark segment repair requires a configured HTTPS object publisher",
                retryable=False,
                submission_unknown=False,
            )
        if frozen_input.get("referencePreparationJobId"):
            images = frozen_input["imageReferences"]
            derived = [item for item in images if item.get("derived")]
            paths = self._resolve_asset_paths(tuple(uuid.UUID(item["assetId"]) for item in derived))
            role_paths = {item["role"]: path for item, path in zip(derived, paths, strict=True)}
            anchor_in = role_paths.get("first_frame" if from_frame else "anchor_in")
            anchor_out = role_paths.get("last_frame" if from_frame else "anchor_out")
            published = None
            context = anchor_in  # Strict frame mode never sends a video.
            if not from_frame:
                video = frozen_input["videoReference"]
                context_id = uuid.UUID(video["assetId"])
                context = self._resolve_asset_paths((context_id,))[0]
                try:
                    published = self._video_reference_publisher.publish_asset(
                        job_id, context_id, context
                    )
                except ObjectPublisherError as exc:
                    raise ProviderGatewayError(
                        code=exc.code,
                        message=exc.message,
                        retryable=False,
                        submission_unknown=False,
                    ) from exc
        else:
            if self._prepare_segment_media is None:
                raise ValueError("segment media preparation is not configured")
            base_asset_id = uuid.UUID(_required_string(frozen_input, "baseVideoAssetId"))
            generation_range = _required_frame_range(frozen_input, "generationRange")
            issue_range = _required_frame_range(frozen_input, "issueRange")
            duration_seconds = int(frozen_input.get("providerDurationSeconds", 0))
            context, anchor_in, anchor_out = self._prepare_segment_media(
                job_id,
                base_asset_id,
                generation_range[0],
                generation_range[1],
                issue_range[0],
                issue_range[1],
                duration_seconds,
            )
            try:
                published = (
                    None if from_frame else self._video_reference_publisher.publish(job_id, context)
                )
            except ObjectPublisherError as exc:
                raise ProviderGatewayError(
                    code=exc.code,
                    message=exc.message,
                    retryable=False,
                    submission_unknown=False,
                ) from exc
        self._prepared_segment_media[job_id] = (
            context,
            anchor_in,
            anchor_out,
            published,
        )

    def submit(
        self,
        *,
        job_id: uuid.UUID,
        kind: str,
        frozen_input: dict[str, object],
    ) -> ProviderSubmission:
        if kind == "plan_video_edit":
            if self._prepare_edit_plan_frames is None:
                raise ValueError("edit plan timeline frame extraction is not configured")
            frames = self._prepared_edit_plan_frames.pop(job_id, None)
            if frames is None:
                frames = self._prepare_edit_plan_frames(job_id, frozen_input)
            result = self._gateway.plan_shots(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
                image_paths=frames,
                input_instruction="图片依次对应冻结的 frameSamples；区分观察事实和不确定建议，按当前文字提出修改建议。",
            )
            return _structured_submission(result)
        if kind == "plan_story":
            result = self._gateway.plan_story(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "plan_shots":
            result = self._gateway.plan_shots(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
                input_instruction=str(
                    frozen_input.get(
                        "inputInstruction",
                        "按顺序比较所有图片并返回诊断。"
                        if frozen_input.get("referenceInputMode") == "vision"
                        else "只返回符合 Schema 的 JSON 对象。",
                    )
                ),
                image_paths=(
                    self._resolve_asset_paths(
                        _uuid_tuple(frozen_input.get("referenceAssetIds", []))
                    )
                    if frozen_input.get("referenceInputMode") == "vision"
                    else ()
                ),
            )
            return _structured_submission(result)
        if kind in {"plan_series", "plan_series_segment"}:
            result = self._gateway.plan_series(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "plan_series_episode":
            result = self._gateway.plan_series_episode(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "analyze_story_source":
            result = self._gateway.analyze_story_source(
                prompt=_required_string(frozen_input, "prompt"),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "generate_image":
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            reference_roles = tuple(
                str(item)
                for item in frozen_input.get("referenceRoles", [])  # type: ignore[union-attr]
            )
            result = self._gateway.generate_image(
                prompt=_required_string(frozen_input, "prompt"),
                negative_prompt=str(frozen_input.get("negativePrompt", ""))
                if frozen_input.get("providerPromptVersion") == 1
                else _required_string(frozen_input, "negativePrompt"),
                reference_paths=self._resolve_asset_paths(reference_ids),
                reference_roles=reference_roles,
                **(
                    {
                        "compiled_provider_prompt": _required_string(
                            frozen_input, "compiledProviderPrompt"
                        )
                    }
                    if frozen_input.get("providerPromptVersion") == 1
                    else {}
                ),
            )
            return ProviderSubmission(
                result={
                    "url": result.url,
                    "responseId": result.response_id,
                    "model": result.model,
                },
                usage=result.usage,
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
            if (
                frozen_input.get("previousEpisodeVideoAssetId") is not None
                and job_id not in self._prepared_video_references
            ):
                self.prepare_submission(job_id=job_id, kind=kind, frozen_input=frozen_input)
            published_video = self._prepared_video_references.pop(job_id, None)
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            reference_roles = tuple(
                str(item)
                for item in frozen_input.get("referenceRoles", [])  # type: ignore[union-attr]
            )
            compiled_prompt = frozen_input.get("compiledProviderPrompt")
            if frozen_input.get("providerPromptVersion") == 1:
                compiled_prompt = _required_string(frozen_input, "compiledProviderPrompt")
            elif not isinstance(compiled_prompt, str) or not compiled_prompt.strip():
                compiled_prompt = compile_provider_video_prompt(
                    prompt=_required_string(frozen_input, "prompt"),
                    negative_prompt=_required_string(frozen_input, "negativePrompt"),
                )
            result = self._gateway.submit_video(
                prompt=compiled_prompt,
                **(
                    {"provider_prompt_version": 1}
                    if frozen_input.get("providerPromptVersion") == 1
                    else {}
                ),
                generation_mode=str(frozen_input.get("generationMode", "references")),
                reference_paths=self._resolve_asset_paths(reference_ids),
                reference_roles=reference_roles,
                reference_video_url=(published_video.url if published_video is not None else None),
                duration_seconds=int(frozen_input.get("durationSeconds", 12)),
                resolution=_required_string(frozen_input, "resolution"),
                **(
                    {"generate_audio": bool(frozen_input["generateAudio"])}
                    if "generateAudio" in frozen_input
                    else {}
                ),
            )
            metadata: dict[str, str] = {}
            if result.request_id:
                metadata["requestId"] = result.request_id
            if published_video is not None:
                metadata["publicationId"] = str(published_video.publication_id)
            return ProviderSubmission(
                taskId=result.task_id,
                metadata=metadata or None,
            )
        if kind == "diagnose_video":
            video_asset_id = uuid.UUID(_required_string(frozen_input, "videoAssetId"))
            timestamps = tuple(
                float(item)
                for item in frozen_input.get("timestampsSeconds", [])  # type: ignore[union-attr]
            )
            frame_paths = self._extract_video_frames(video_asset_id, timestamps)
            reference_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            result = self._gateway.diagnose(
                prompt=_required_string(frozen_input, "prompt"),
                image_paths=(*self._resolve_asset_paths(reference_ids), *frame_paths),
                output_schema=_required_dict(frozen_input, "outputSchema"),
            )
            return _structured_submission(result)
        if kind == "regenerate_video_segment":
            if job_id not in self._prepared_segment_media:
                self.prepare_submission(job_id=job_id, kind=kind, frozen_input=frozen_input)
            _context, anchor_in, anchor_out, published = self._prepared_segment_media.pop(job_id)
            duration_seconds = int(frozen_input.get("providerDurationSeconds", 0))
            reference_roles = tuple(
                str(item)
                for item in frozen_input.get("referenceRoles", [])  # type: ignore[union-attr]
            )
            from_frame = frozen_input.get("generationMode") == "from_frame"
            v2 = frozen_input.get("editContractVersion") == 2
            if from_frame:
                issue = _required_frame_range(frozen_input, "issueRange")
                strict_last = frozen_input.get("anchorEndFrame") is not None and (
                    not v2 or issue[1] - issue[0] == duration_seconds * 24
                )
                expected_roles = ("first_frame", *(("last_frame",) if strict_last else ()))
            elif v2:
                expected_roles = (
                    *(("anchor_in",) if frozen_input.get("includeInAnchor", True) else ()),
                    *(
                        ("anchor_out",)
                        if frozen_input.get("endStatePolicy") == "match_original"
                        else ()
                    ),
                    *(role for role in CANON_ROLES if role in reference_roles),
                )
            else:
                expected_roles = (
                    "anchor_in",
                    *(("anchor_out",) if frozen_input.get("endStatePolicy") != "replace" else ()),
                    *CANON_ROLES,
                )
            if (
                v2
                and tuple(item["role"] for item in frozen_input["imageReferences"])
                != expected_roles
            ):
                raise ValueError("frozen images do not match selected reference roles")
            if reference_roles != expected_roles:
                raise ValueError("segment reference roles are incomplete or out of order")
            canon_ids = _uuid_tuple(frozen_input.get("referenceAssetIds", []))
            canon_roles = (
                tuple(
                    item["role"]
                    for item in frozen_input["imageReferences"]
                    if not item.get("derived")
                )
                if v2
                else (() if from_frame else reference_roles[-5:])
            )
            if len(canon_ids) != len(canon_roles):
                raise ValueError("stored reference count does not match ordered Canon roles")
            compiler_revision = str(frozen_input.get("promptCompilerRevision", "segment-edit-v2"))
            time_origin = (
                _required_frame_range(frozen_input, "generationRange")[0]
                if compiler_revision
                in {
                    "segment-edit-v3",
                    "segment-edit-v4",
                    "segment-edit-v5",
                    "segment-edit-v6",
                    "segment-edit-v7",
                    "segment-edit-v6-performance",
                    "segment-edit-v8-performance",
                }
                else 0
            )
            result = self._gateway.submit_segment_video(
                SegmentVideoGenerationRequest(
                    instruction=_required_string(
                        frozen_input, "instruction", preserve_whitespace=v2
                    ),
                    prompt=_required_string(frozen_input, "prompt", preserve_whitespace=v2),
                    negative_prompt=str(frozen_input.get("negativePrompt", ""))
                    if v2
                    else _required_string(frozen_input, "negativePrompt"),
                    context_video_url=published.url if published else None,
                    generation_mode="from_frame" if from_frame else "edit_existing",
                    generate_audio=bool(frozen_input.get("generateAudio", False)),
                    issue_start_seconds=(
                        _required_frame_range(frozen_input, "issueRange")[0] - time_origin
                    )
                    / 24,
                    issue_end_seconds=(
                        _required_frame_range(frozen_input, "issueRange")[1] - time_origin
                    )
                    / 24,
                    anchor_in_path=anchor_in,
                    anchor_out_path=anchor_out
                    if any(role in reference_roles for role in ("anchor_out", "last_frame"))
                    else None,
                    canon_reference_paths=self._resolve_asset_paths(canon_ids),
                    canon_reference_roles=canon_roles,
                    duration_seconds=duration_seconds,
                    resolution="480p",
                    ratio="9:16",
                    prompt_compiler_revision=compiler_revision,
                    compiled_provider_prompt=(
                        _required_string(
                            frozen_input, "compiledProviderPrompt", preserve_whitespace=v2
                        )
                        if frozen_input.get("providerPromptVersion") == 1
                        else None
                    ),
                )
            )
            metadata = {"publicationId": str(published.publication_id)} if published else {}
            if result.request_id:
                metadata["requestId"] = result.request_id
            return ProviderSubmission(taskId=result.task_id, metadata=metadata)
        raise ValueError(f"Ark does not own CatFlow job kind: {kind}")

    def poll(self, provider_task_id: str) -> ProviderPoll:
        result = self._gateway.poll_video(provider_task_id)
        if result.status in {"running", "unknown"}:
            return ProviderPoll(
                status=result.status, provider_status=result.provider_status, error=result.error
            )
        if result.status == "failed":
            return ProviderPoll(
                status="failed", error=result.error, provider_status=result.provider_status
            )
        return ProviderPoll(
            status="succeeded",
            provider_status=result.provider_status,
            result={
                "videoUrl": result.video_url,
                "lastFrameUrl": result.last_frame_url,
                "model": result.model,
                "durationSeconds": result.duration_seconds,
                "ratio": result.ratio,
                "resolution": result.resolution,
            },
            usage=result.usage,
        )

    def poll_response(self, response_id: str) -> ProviderPoll:
        from .ark_responses import response_usage

        document = self._gateway.retrieve_response(response_id)
        status = document.get("status")
        if status in {"queued", "in_progress"}:
            return ProviderPoll(status="running", provider_status=status)
        if status == "completed":
            # Parsing is local work. Query success must survive malformed JSON.
            return ProviderPoll(
                status="succeeded",
                provider_status=status,
                result={"rawResponse": document},
                usage=response_usage(document),
            )
        if status in {"failed", "incomplete", "cancelled", "expired"}:
            return ProviderPoll(
                status="failed",
                provider_status=status,
                error=document.get("error")
                or {"code": status, "message": str(document.get("incomplete_details") or status)},
                result={"rawResponse": document},
                usage=response_usage(document),
            )
        return ProviderPoll(
            status="unknown",
            provider_status=status,
            error={"code": "provider_state_unrecognized", "message": f"外部状态：{status}"},
        )

    def restore_result(self, kind: str, document: dict[str, object]) -> dict[str, object]:
        from catflow.application.job_execution import TEXT_JOB_KINDS, VIDEO_JOB_KINDS

        from .ark_responses import parse_response

        if kind in TEXT_JOB_KINDS:
            return parse_response(document)
        if kind in VIDEO_JOB_KINDS:
            content = document.get("content") or {}
            if not content.get("video_url"):
                raise ValueError("外部已生成，但回执没有视频下载地址。")
            return {
                "videoUrl": content["video_url"],
                "lastFrameUrl": content.get("last_frame_url"),
                "model": document.get("model"),
                "durationSeconds": document.get("duration"),
                "ratio": document.get("ratio"),
                "resolution": document.get("resolution"),
            }
        data = document.get("data") or []
        if len(data) != 1 or not data[0].get("url"):
            raise ValueError("生图回执没有一张可下载的图片；原始正文已保留。")
        return {"url": data[0]["url"], "model": document.get("model")}


def _structured_submission(result: StructuredProviderResult) -> ProviderSubmission:
    document: dict[str, object] = {
        "payload": result.payload,
        "responseId": result.response_id,
        "model": result.model,
        "requestHash": result.request_hash,
    }
    # 解析层确定性修复审计:仅在确有修复时落库,保持严格解析回执形状不变
    if result.text_repairs:
        document["textRepairs"] = list(result.text_repairs)
    return ProviderSubmission(result=document, usage=result.usage)


def _required_string(
    document: dict[str, object], key: str, *, preserve_whitespace: bool = False
) -> str:
    value = document.get(key, "")
    if preserve_whitespace and not isinstance(value, str):
        raise ValueError(f"frozen Ark input requires string {key}")
    value = str(value)
    if not value.strip():
        raise ValueError(f"frozen Ark input requires {key}")
    return value if preserve_whitespace else value.strip()


def _required_dict(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"frozen Ark input requires object {key}")
    return value


def _uuid_tuple(value: object) -> tuple[uuid.UUID, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("frozen Ark asset IDs must be an array")
    return tuple(uuid.UUID(str(item)) for item in value)


def _required_frame_range(document: dict[str, object], key: str) -> tuple[int, int]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"frozen Ark input requires object {key}")
    start = value.get("startFrame")
    end = value.get("endFrame")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        raise ValueError(f"frozen Ark input has invalid {key}")
    return start, end
