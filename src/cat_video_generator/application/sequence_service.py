"""Local composition of approved shot videos into an auditable project sequence."""

from __future__ import annotations

import hashlib
import math
import uuid

from ..domain.rendering import (
    ProjectSequencePlan,
    SequenceClip,
    SequenceStatus,
    SequenceTransition,
    SequenceTransitionType,
)
from .ports import (
    AssetStore,
    MediaProbe,
    RuntimePreflight,
    ShotQueueStore,
    StoredAsset,
    StoredSequence,
    StoredShot,
)


class SequenceService:
    """Own FFmpeg composition, transition validation, QC, and sequence versioning."""

    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        resolution: str,
        runtime_preflight: RuntimePreflight | None = None,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._resolution = resolution
        self._runtime_preflight = runtime_preflight

    @property
    def _video_resolution(self) -> str:
        if self._runtime_preflight is not None:
            return self._runtime_preflight.video_resolution
        return self._resolution

    def build_project_sequence(
        self,
        project_id: uuid.UUID,
        *,
        transitions: dict[uuid.UUID, SequenceTransition] | None = None,
        intro_transition: SequenceTransition | None = None,
        outro_transition: SequenceTransition | None = None,
        request_idempotency_key: str | None = None,
    ) -> StoredSequence:
        prior_sequences = self._repository.list_sequences(project_id)
        request_idempotency_hash = (
            hashlib.sha256(request_idempotency_key.encode("utf-8")).hexdigest()
            if request_idempotency_key is not None
            else None
        )
        if request_idempotency_hash is not None:
            for prior in reversed(prior_sequences):
                if prior.rendered_asset_id is None:
                    continue
                rendered = self._repository.get_asset(prior.rendered_asset_id)
                if rendered.metadata.get("requestIdempotencyHash") == request_idempotency_hash:
                    return prior

        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_local_composition()
        scenes = self._repository.list_scenes(project_id)
        selected: list[tuple[StoredShot, StoredAsset, int]] = []
        for scene in scenes:
            for shot in self._repository.list_shots(scene.id):
                if shot.selected_video_asset_id is None:
                    continue
                asset = self._repository.get_asset(shot.selected_video_asset_id)
                duration_ms = int(asset.metadata.get("qc", {}).get("durationMs") or 0)
                if duration_ms <= 0:
                    raise ValueError("selected video is missing QC duration")
                selected.append((shot, asset, duration_ms))
        if not selected:
            raise ValueError("a project sequence requires at least one approved shot video")
        transitions = transitions or {}
        valid_transition_ids = {item[0].id for item in selected[1:]}
        if set(transitions) - valid_transition_ids:
            raise ValueError("sequence transition references a non-following shot")

        clips: list[SequenceClip] = []
        cursor = 0
        for order, (shot, asset, duration_ms) in enumerate(selected, 1):
            transition = None if order == 1 else transitions.get(shot.id, SequenceTransition())
            overlap = (
                transition.duration_ms
                if transition is not None
                and transition.type is SequenceTransitionType.CROSS_DISSOLVE
                else 0
            )
            timeline_start = cursor - overlap
            clips.append(
                SequenceClip(
                    order=order,
                    shot_card_id=shot.id,
                    source_asset_id=asset.id,
                    source_start_ms=0,
                    source_end_ms=duration_ms,
                    timeline_start_ms=timeline_start,
                    timeline_end_ms=timeline_start + duration_ms,
                    transitionFromPrevious=transition,
                )
            )
            cursor = timeline_start + duration_ms

        plan = ProjectSequencePlan(
            duration_ms=cursor,
            clips=clips,
            introTransition=intro_transition,
            outroTransition=outro_transition,
        )
        landed = self._asset_store.compose_sequence(
            tuple(item[1].require_path() for item in selected),
            plan,
        )
        qc = self._media_probe.inspect_video(
            landed.path,
            expected_duration_seconds=round(cursor / 1000),
            expected_resolution=self._video_resolution,
            minimum_duration_seconds=1,
            maximum_duration_seconds=max(15, math.ceil(cursor / 1000) + 1),
            duration_tolerance_ms=1500,
        )
        if not qc.get("passed"):
            raise ValueError(f"project sequence QC failed: {qc.get('failures')}")
        asset = self._repository.add_asset(
            landed=landed,
            role="project_sequence",
            media_type="video",
            scope="project",
            status="candidate",
            project_id=project_id,
            scene_id=None,
            shot_id=None,
            step_id=None,
            semantic_key=f"project:{project_id}:sequence",
            metadata={
                "qc": qc,
                "audioPolicy": "transition_plan",
                "requestIdempotencyHash": request_idempotency_hash,
                "transitions": [
                    None
                    if item.transition_from_previous is None
                    else item.transition_from_previous.model_dump(mode="json", by_alias=True)
                    for item in plan.clips
                ],
                "introTransition": (
                    None
                    if plan.intro_transition is None
                    else plan.intro_transition.model_dump(mode="json", by_alias=True)
                ),
                "outroTransition": (
                    None
                    if plan.outro_transition is None
                    else plan.outro_transition.model_dump(mode="json", by_alias=True)
                ),
            },
        )
        project = self._repository.get_project(project_id)
        parent_sequence_id = project.selected_sequence_id or (
            prior_sequences[-1].id if prior_sequences else None
        )
        return self._repository.create_sequence(
            project_id=project_id,
            plan=plan,
            parent_sequence_id=parent_sequence_id,
            rendered_asset_id=asset.id,
            status=SequenceStatus.CONTENT_REVIEW,
        )
