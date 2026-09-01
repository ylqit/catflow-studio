from __future__ import annotations

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from cat_video_generator.domain.production_recipes import (
    GenerationPlanStatus,
    StoryboardRevisionStatus,
)
from cat_video_generator.infrastructure.db.models import (
    Asset,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationPlan,
    ProductionRun,
    Scene,
    ShotBeat,
    ShotCard,
    StoryboardRevision,
    StoryRevisionRecord,
    VideoSequence,
)


def story_revision_contract_kind(
    row: StoryRevisionRecord,
    *,
    legacy_score_present: bool = False,
) -> str:
    if row.parent_revision_id is not None:
        return "creative_text"
    if row.source_event_candidate_id is not None or row.scene_plan_json or legacy_score_present:
        return "legacy_structured"
    return "creative_text"


def requires_legacy_story_approval_contract(row: StoryRevisionRecord) -> bool:
    return story_revision_contract_kind(row) == "legacy_structured"


def invalidate_story_production_lineage(
    session: Session,
    *,
    project_id: uuid.UUID,
    story_ids: tuple[uuid.UUID, ...],
    reason: str,
) -> None:
    if not story_ids:
        return
    scene_ids = select(Scene.id).where(Scene.story_revision_id.in_(story_ids))
    storyboard_ids = select(StoryboardRevision.id).where(
        StoryboardRevision.story_revision_id.in_(story_ids)
    )
    generation_plan_ids = select(GenerationPlan.id).where(
        GenerationPlan.storyboard_revision_id.in_(storyboard_ids)
    )
    character_asset_ids = (
        select(CharacterDesignAsset.asset_id)
        .join(
            CharacterDesignRevision,
            CharacterDesignRevision.id == CharacterDesignAsset.character_design_revision_id,
        )
        .where(CharacterDesignRevision.production_run_id == project_id)
    )
    session.execute(
        update(Scene)
        .where(Scene.story_revision_id.in_(story_ids), Scene.active.is_(True))
        .values(active=False, stale_reason=reason)
    )
    session.execute(
        update(ShotBeat)
        .where(
            ShotBeat.story_revision_id.in_(story_ids),
            ShotBeat.status != "superseded",
        )
        .values(status="stale", stale_reason=reason)
    )
    session.execute(
        update(StoryboardRevision)
        .where(
            StoryboardRevision.id.in_(storyboard_ids),
            StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
        )
        .values(status=StoryboardRevisionStatus.SUPERSEDED.value)
    )
    session.execute(
        update(GenerationPlan)
        .where(GenerationPlan.id.in_(generation_plan_ids))
        .values(status=GenerationPlanStatus.STALE.value)
    )
    session.execute(
        update(ShotCard)
        .where(
            or_(
                ShotCard.scene_id.in_(scene_ids),
                ShotCard.generation_plan_id.in_(generation_plan_ids),
            )
        )
        .values(
            prompt_id=None,
            selected_anchor_asset_id=None,
            selected_video_asset_id=None,
            status="ready",
        )
    )
    session.execute(
        update(Asset)
        .where(
            Asset.production_run_id == project_id,
            Asset.scope != "canon",
            Asset.id.not_in(character_asset_ids),
            Asset.status != "stale",
        )
        .values(status="stale")
    )
    session.execute(
        update(VideoSequence)
        .where(VideoSequence.production_run_id == project_id)
        .values(status="rejected")
    )
    project = session.get(ProductionRun, project_id)
    if project is not None:
        project.selected_sequence_id = None
