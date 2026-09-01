"""Version markers for the staged creative workflow.

The hash deliberately covers only user-controlled scene inputs that affect story
analysis. Provider output and generated media are immutable audit history and do
not participate in this optimistic-concurrency marker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from uuid import UUID

from .contracts import SceneDraft, ShotCardDraft


def story_source_hash(scene: SceneDraft) -> str:
    payload = {
        "title": scene.title,
        "sourceText": scene.source_text,
        "contextNote": scene.context_note,
        "storyMode": scene.story_mode.value,
        "targetShotCount": scene.target_shot_count,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def shot_snapshot_hash(
    shots: Iterable[tuple[UUID, int, ShotCardDraft]],
) -> str:
    """Stable optimistic-concurrency marker for an ordered set of shot drafts."""

    payload = [
        {
            "id": str(shot_id),
            "draftRevision": draft_revision,
            "draft": draft.model_dump(mode="json", by_alias=True),
        }
        for shot_id, draft_revision, draft in shots
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
