"""Canonical storyboard snapshot hashing shared by edit and review boundaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .models import ShotBeat


def storyboard_structure_hash(beats: Sequence[ShotBeat]) -> str:
    """Hash every persisted editorial fact that can affect a plan or prompt.

    Reference bindings remain a separately editable asset boundary, but their
    revision and canonical payload are part of the storyboard snapshot a human
    confirms.  Callers must pass beats in their executable order.
    """

    document = [
        {
            "id": str(beat.id),
            "revision": beat.revision,
            "storyboardRevisionId": (
                None
                if beat.storyboard_revision_id is None
                else str(beat.storyboard_revision_id)
            ),
            "storyRevisionId": (
                None if beat.story_revision_id is None else str(beat.story_revision_id)
            ),
            "sceneId": str(beat.scene_id),
            "order": beat.sort_order,
            "title": beat.title,
            "direction": beat.action,
            "visualDescription": beat.visual_description,
            "childAction": beat.child_action,
            "catAction": beat.cat_action,
            "spatialRelation": beat.spatial_relation,
            "contactOcclusion": beat.contact_occlusion,
            "shotSize": beat.shot_size,
            "camera": beat.camera,
            "lighting": beat.lighting,
            "dialogue": beat.dialogue,
            "soundEffect": beat.sound_effect,
            "musicIntent": beat.music_intent,
            "wardrobeState": beat.wardrobe_state,
            "propState": beat.prop_state,
            "continuityIn": beat.continuity_in,
            "continuityOut": beat.continuity_out,
            "cutIntent": beat.cut_intent,
            "durationSeconds": beat.duration_seconds,
            "temporalBeats": list(beat.temporal_beats_json or []),
            "referenceBindings": list(beat.reference_bindings_json or []),
            "referenceBindingRevision": beat.reference_binding_revision,
        }
        for beat in beats
    ]
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def generation_plan_input_hash(
    *,
    structure_hash: str,
    provider: str,
    model: str,
    capability_revision: str,
    clips: Sequence[Mapping[str, Any]],
) -> str:
    """Hash the executable plan snapshot against one storyboard structure."""

    document = {
        "structureHash": structure_hash,
        "provider": provider,
        "model": model,
        "capabilityRevision": capability_revision,
        "clips": [
            {
                "durationSeconds": int(clip["durationSeconds"]),
                "shotBeatIds": [str(beat_id) for beat_id in clip["shotBeatIds"]],
            }
            for clip in clips
        ],
    }
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
