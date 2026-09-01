"""Serialization helpers for batch-loaded, read-only production projections."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from ..domain.contracts import CURRENT_CONTRACT_VERSION
from .ports import (
    ProjectReadModel,
    StoredAsset,
    StoredPrompt,
    StoredReview,
    StoredShot,
    StoredStep,
)


def asset_projection(asset: StoredAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "role": asset.role,
        "mediaType": asset.media_type,
        "scope": asset.scope,
        "status": asset.status,
        "projectId": None if asset.project_id is None else str(asset.project_id),
        "sceneId": None if asset.scene_id is None else str(asset.scene_id),
        "shotId": None if asset.shot_card_id is None else str(asset.shot_card_id),
        "producingStepId": None if asset.step_id is None else str(asset.step_id),
        "sha256": asset.sha256,
        "semanticKey": asset.semantic_key,
        "metadata": asset.metadata,
        "contentReady": asset.content_ready,
        "displayName": asset.display_name,
        "referencePurpose": asset.reference_purpose,
        "visualProfileRevisionId": asset.metadata.get("visualProfileRevisionId"),
        "lookDraftRevision": asset.metadata.get("lookDraftRevision"),
        "createdAt": None if asset.created_at is None else asset.created_at.isoformat(),
    }


def step_projection(
    step: StoredStep,
    *,
    prompt: StoredPrompt | None = None,
    reviews: Iterable[StoredReview] = (),
) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "kind": step.kind.value,
        "status": step.status.value,
        "attempt": step.attempt,
        "operationKey": step.operation_key,
        "provider": step.provider,
        "providerTaskId": step.provider_task_id,
        "model": step.model,
        "inputSnapshot": step.input_snapshot,
        "error": step.error,
        "createdAt": None if step.created_at is None else step.created_at.isoformat(),
        "prompt": (
            None
            if prompt is None
            else {
                "id": str(prompt.id),
                "purpose": prompt.purpose.value,
                "model": prompt.model,
                "text": prompt.text,
                "sha256": prompt.sha256,
            }
        ),
        "reviews": [
            {
                "id": str(review.id),
                "source": review.source,
                "decision": review.decision,
                "reason": review.reason,
                "warnings": list(review.warnings),
                "evidence": review.evidence,
            }
            for review in reviews
        ],
    }


def shot_projection(
    shot: StoredShot,
    *,
    assets: Iterable[StoredAsset] = (),
    attempts: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "id": str(shot.id),
        "sceneId": str(shot.scene_id),
        "order": shot.order,
        **shot.draft.model_dump(mode="json", by_alias=True),
        "draftRevision": shot.draft_revision,
        "useSceneLook": shot.draft.use_scene_look,
        "status": shot.status.value,
        "selectedAnchorAssetId": (
            None
            if shot.selected_anchor_asset_id is None
            else str(shot.selected_anchor_asset_id)
        ),
        "selectedVideoAssetId": (
            None
            if shot.selected_video_asset_id is None
            else str(shot.selected_video_asset_id)
        ),
        "assets": [asset_projection(item) for item in assets],
        "attempts": list(attempts),
    }


def project_graph_projection(model: ProjectReadModel) -> dict[str, Any]:
    """Serialize one already-loaded project graph without repository fan-out."""

    prompts = {item.step_id: item for item in model.prompts}
    reviews_by_step: dict[UUID, list[StoredReview]] = {}
    steps_by_scene: dict[UUID, list[StoredStep]] = {}
    steps_by_shot: dict[UUID, list[StoredStep]] = {}
    shots_by_scene: dict[UUID, list[StoredShot]] = {}
    assets_by_shot: dict[UUID, list[StoredAsset]] = {}
    for review in model.reviews:
        reviews_by_step.setdefault(review.step_id, []).append(review)
    for step in model.steps:
        if step.shot_card_id is not None:
            steps_by_shot.setdefault(step.shot_card_id, []).append(step)
        elif step.scene_id is not None:
            steps_by_scene.setdefault(step.scene_id, []).append(step)
    for shot in model.shots:
        shots_by_scene.setdefault(shot.scene_id, []).append(shot)
    for asset in model.assets:
        if asset.shot_card_id is not None:
            assets_by_shot.setdefault(asset.shot_card_id, []).append(asset)

    def attempt(step: StoredStep) -> dict[str, Any]:
        return step_projection(
            step,
            prompt=prompts.get(step.id),
            reviews=reviews_by_step.get(step.id, []),
        )

    project = model.project
    return {
        "project": {
            "id": str(project.id),
            "title": project.title,
            "contentDate": project.content_date.isoformat(),
            "status": project.status.value,
            "selectedSequenceId": (
                None
                if project.selected_sequence_id is None
                else str(project.selected_sequence_id)
            ),
            "contractVersion": CURRENT_CONTRACT_VERSION,
            "visualProfileRevisionId": (
                None
                if project.visual_profile_revision_id is None
                else str(project.visual_profile_revision_id)
            ),
            "defaultReferenceBindings": [
                item.model_dump(mode="json", by_alias=True)
                for item in project.default_reference_bindings
            ],
        },
        "assets": [asset_projection(item) for item in model.assets],
        "scenes": [
            {
                "id": str(scene.id),
                "order": scene.order,
                **scene.draft.model_dump(mode="json", by_alias=True),
                "status": scene.status.value,
                "selectedLookAssetId": (
                    None
                    if scene.selected_look_asset_id is None
                    else str(scene.selected_look_asset_id)
                ),
                "lookDraftRevision": scene.look_draft_revision,
                "attempts": [
                    attempt(step) for step in steps_by_scene.get(scene.id, [])
                ],
                "shots": [
                    shot_projection(
                        shot,
                        assets=assets_by_shot.get(shot.id, []),
                        attempts=[
                            attempt(step)
                            for step in steps_by_shot.get(shot.id, [])
                        ],
                    )
                    for shot in shots_by_scene.get(scene.id, [])
                ],
            }
            for scene in model.scenes
        ],
        "sequences": [
            {
                "id": str(item.id),
                "projectId": str(item.project_id),
                "revision": item.revision,
                "parentSequenceId": (
                    None
                    if item.parent_sequence_id is None
                    else str(item.parent_sequence_id)
                ),
                "renderedAssetId": (
                    None
                    if item.rendered_asset_id is None
                    else str(item.rendered_asset_id)
                ),
                "status": item.status.value,
                "plan": item.plan.model_dump(mode="json"),
            }
            for item in model.sequences
        ],
    }
