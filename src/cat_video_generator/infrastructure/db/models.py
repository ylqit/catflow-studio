"""V5场景、视频片段、造型与相对资产存储的PostgreSQL模型。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ...domain.contracts import CURRENT_CONTRACT_VERSION, SceneLookUsage
from ...domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
)

SCHEMA_NAME = "cat_video"


def _values(items: type) -> tuple[str, ...]:
    return tuple(item.value for item in items)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    """所有数据库表的元数据根。"""


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        CheckConstraint(_check("status", _values(RunStatus)), name="ck_production_runs_status"),
        CheckConstraint("contract_version = 5", name="ck_production_runs_contract_version"),
        Index("ix_production_runs_queue", "status", "content_date", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content_date: Mapped[date] = mapped_column(Date, nullable=False)
    contract_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=CURRENT_CONTRACT_VERSION,
        server_default=text(str(CURRENT_CONTRACT_VERSION)),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.ACTIVE.value)
    canvas_v2_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    canvas_template_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="short_drama",
        server_default="short_drama",
    )
    universal_canvas_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    product_ad_template_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    video_edit_v2_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    current_visual_profile_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.visual_profile_revisions.id",
            name="fk_production_runs_visual_profile_revision",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    selected_sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.video_sequences.id",
            name="fk_production_runs_selected_sequence",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProductionRecipeInstance(Base):
    __tablename__ = "production_recipe_instances"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_production_recipe_instances_revision"),
        CheckConstraint(
            "target_duration_seconds BETWEEN 8 AND 60",
            name="ck_production_recipe_instances_duration",
        ),
        CheckConstraint(
            "quality_tier IN ('quick', 'balanced', 'premium')",
            name="ck_production_recipe_instances_quality_tier",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_production_recipe_instances_lifecycle",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    recipe_key: Mapped[str] = mapped_column(String(80), nullable=False)
    recipe_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    inspiration_key: Mapped[str | None] = mapped_column(String(80))
    target_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    canon_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CharacterDesignRevision(Base):
    __tablename__ = "character_design_revisions"
    __table_args__ = (
        UniqueConstraint(
            "production_recipe_instance_id",
            "revision",
            name="uq_character_design_revisions_instance_revision",
        ),
        UniqueConstraint("idempotency_key", name="uq_character_design_revisions_idempotency"),
        CheckConstraint(
            "status IN ('generating', 'awaiting_review', 'approved', 'stale')",
            name="ck_character_design_revisions_status",
        ),
        Index(
            "ix_character_design_revisions_current",
            "production_recipe_instance_id",
            "revision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_recipe_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_recipe_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_story_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="generating", server_default="generating"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CharacterDesignAsset(Base):
    __tablename__ = "character_design_assets"
    __table_args__ = (
        UniqueConstraint(
            "character_design_revision_id",
            "slot",
            "candidate_index",
            name="uq_character_design_assets_slot_candidate",
        ),
        UniqueConstraint("asset_id", name="uq_character_design_assets_asset"),
        CheckConstraint(
            "slot IN ('child', 'cat', 'pair_scale')",
            name="ck_character_design_assets_slot",
        ),
        CheckConstraint("candidate_index >= 1", name="ck_character_design_assets_candidate"),
        Index(
            "ix_character_design_assets_revision_slot",
            "character_design_revision_id",
            "slot",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_design_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.character_design_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    slot: Mapped[str] = mapped_column(String(24), nullable=False)
    candidate_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semantic_role: Mapped[str] = mapped_column(String(40), nullable=False)
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HumanReviewDecisionRecord(Base):
    __tablename__ = "human_review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'request_changes', 'override')",
            name="ck_human_review_decisions_decision",
        ),
        CheckConstraint(
            "target_revision IS NOT NULL OR target_hash IS NOT NULL",
            name="ck_human_review_decisions_pinned_target",
        ),
        CheckConstraint(
            "NOT (decision = 'approve' AND blocking_diagnostic_present)",
            name="ck_human_review_decisions_blocking_approval",
        ),
        CheckConstraint(
            "decision != 'override' OR NULLIF(BTRIM(reason), '') IS NOT NULL",
            name="ck_human_review_decisions_override_reason",
        ),
        Index(
            "ix_human_review_decisions_target",
            "production_recipe_instance_id",
            "target_type",
            "target_id",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_recipe_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_recipe_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_revision: Mapped[int | None] = mapped_column(Integer)
    target_hash: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    blocking_diagnostic_present: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    issues_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VisualProfileRevision(Base):
    __tablename__ = "visual_profile_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_visual_profile_revisions_revision"),
        UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_visual_profile_revisions_run_revision",
        ),
        UniqueConstraint(
            "production_run_id",
            "profile_hash",
            name="uq_visual_profile_revisions_run_hash",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    person_identity: Mapped[str] = mapped_column(Text, nullable=False)
    person_hair: Mapped[str] = mapped_column(Text, nullable=False)
    person_body: Mapped[str] = mapped_column(Text, nullable=False)
    cat_identity: Mapped[str] = mapped_column(Text, nullable=False)
    style_positive_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    style_negative_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reference_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        CheckConstraint(_check("status", _values(SceneStatus)), name="ck_scenes_status"),
        CheckConstraint("sort_order >= 1", name="ck_scenes_sort_order"),
        CheckConstraint(
            "(story_mode = 'single' AND target_shot_count = 1) OR "
            "(story_mode = 'multi' AND target_shot_count BETWEEN 2 AND 6)",
            name="ck_scenes_story_shape",
        ),
        UniqueConstraint(
            "story_revision_id",
            "scene_key",
            name="uq_scenes_story_revision_key",
        ),
        Index(
            "uq_scenes_active_run_order",
            "production_run_id",
            "sort_order",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
        Index("ix_scenes_run_status", "production_run_id", "status", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.story_revisions.id",
            ondelete="SET NULL",
            name="fk_scenes_story_revision",
        ),
    )
    scene_key: Mapped[str | None] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    stale_reason: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_label: Mapped[str | None] = mapped_column(String(80))
    context_note: Mapped[str | None] = mapped_column(Text)
    story_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="single", server_default="single"
    )
    target_shot_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    look_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    look_draft_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    look_draft_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    selected_look_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_scenes_selected_look_asset",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=SceneStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ShotCard(Base):
    __tablename__ = "shot_cards"
    __table_args__ = (
        CheckConstraint(_check("status", _values(ShotStatus)), name="ck_shot_cards_status"),
        CheckConstraint("sort_order >= 1", name="ck_shot_cards_sort_order"),
        CheckConstraint(
            "plan_sort_order IS NULL OR plan_sort_order >= 1",
            name="ck_shot_cards_plan_sort_order",
        ),
        CheckConstraint("duration_seconds BETWEEN 1 AND 60", name="ck_shot_cards_duration"),
        CheckConstraint(
            "anchor_mode IN ('text_only', 'existing', 'generate')",
            name="ck_shot_cards_anchor_mode",
        ),
        CheckConstraint(
            _check("scene_look_usage", _values(SceneLookUsage)),
            name="ck_shot_cards_scene_look_usage",
        ),
        UniqueConstraint(
            "generation_plan_id",
            "plan_sort_order",
            name="uq_shot_cards_generation_plan_order",
        ),
        Index("ix_shot_cards_scene_status", "scene_id", "status", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_sort_order: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=8)
    generation_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.generation_plans.id",
            name="fk_shot_cards_generation_plan",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.prompt_records.id",
            name="fk_shot_cards_prompt",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    anchor_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="text_only")
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    inherit_project_references: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    use_scene_look: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    draft_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    scene_look_usage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SceneLookUsage.APPEARANCE_ONLY.value,
        server_default=SceneLookUsage.APPEARANCE_ONLY.value,
    )
    selected_anchor_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_shot_cards_selected_anchor",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    selected_video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_shot_cards_selected_video",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ShotStatus.READY.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint(_check("kind", _values(StepKind)), name="ck_workflow_steps_kind"),
        CheckConstraint(_check("status", _values(StepStatus)), name="ck_workflow_steps_status"),
        CheckConstraint("attempt >= 1", name="ck_workflow_steps_attempt"),
        UniqueConstraint("idempotency_key", name="uq_workflow_steps_idempotency"),
        Index(
            "uq_workflow_steps_provider_task",
            "provider_task_id",
            unique=True,
            postgresql_where=text("provider_task_id IS NOT NULL"),
        ),
        Index("ix_workflow_steps_resume", "status", "kind", "created_at"),
        Index(
            "ix_workflow_steps_claim",
            "status",
            "next_retry_at",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "uq_workflow_steps_shot_attempt",
            "shot_card_id",
            "operation_key",
            "attempt",
            unique=True,
            postgresql_where=text("shot_card_id IS NOT NULL"),
        ),
        Index(
            "uq_workflow_steps_scene_attempt",
            "scene_id",
            "operation_key",
            "attempt",
            unique=True,
            postgresql_where=text("scene_id IS NOT NULL AND shot_card_id IS NULL"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    shot_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="CASCADE")
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="CASCADE"),
    )
    generation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_snapshots.id", ondelete="RESTRICT"),
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StepStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    operation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    progress_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    retry_chain_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PromptRecord(Base):
    __tablename__ = "prompt_records"
    __table_args__ = (
        CheckConstraint(
            _check("purpose", tuple(item.value for item in PromptPurpose)),
            name="ck_prompt_records_purpose",
        ),
        UniqueConstraint("step_id", "sha256", name="uq_prompt_records_step_hash"),
        Index("ix_prompt_records_sha256", "sha256"),
        Index(
            "ix_prompt_records_business_object",
            "business_object_type",
            "business_object_id",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    call_purpose: Mapped[str | None] = mapped_column(String(120))
    node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    business_object_type: Mapped[str | None] = mapped_column(String(80))
    business_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL"),
    )
    template_name: Mapped[str | None] = mapped_column(String(160))
    template_version: Mapped[str | None] = mapped_column(String(80))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    user_prompt: Mapped[str | None] = mapped_column(Text)
    final_prompt: Mapped[str | None] = mapped_column(Text)
    provider_request_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    provider_internal_transform: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_observable",
        server_default="not_observable",
    )
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    raw_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    structured_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    accepted_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_diff_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    token_usage_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="succeeded", server_default="succeeded"
    )
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoryBriefRecord(Base):
    __tablename__ = "story_briefs"
    __table_args__ = (
        UniqueConstraint("production_run_id", "revision", name="uq_story_briefs_run_revision"),
        Index("ix_story_briefs_current", "production_run_id", "revision"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(String(300), nullable=False)
    genre: Mapped[str] = mapped_column(String(200), nullable=False)
    tone: Mapped[str] = mapped_column(String(300), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    constraints_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        Index("ix_subjects_run_role", "production_run_id", "role", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft"
    )
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.subject_revisions.id",
            name="fk_subjects_current_revision",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SubjectRevision(Base):
    __tablename__ = "subject_revisions"
    __table_args__ = (
        UniqueConstraint("subject_id", "revision", name="uq_subject_revisions_subject_revision"),
        UniqueConstraint("subject_id", "revision_hash", name="uq_subject_revisions_subject_hash"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_anchors_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    immutable_traits_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    relationship_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dramatic_function: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visual_risks_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubjectReference(Base):
    __tablename__ = "subject_references"
    __table_args__ = (
        UniqueConstraint(
            "subject_revision_id",
            "asset_id",
            "semantic_role",
            name="uq_subject_references_binding",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subject_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    semantic_role: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")


class StoryEventCandidateRecord(Base):
    __tablename__ = "story_event_candidates"
    __table_args__ = (
        UniqueConstraint(
            "production_recipe_instance_id",
            "batch_id",
            "candidate_index",
            name="uq_story_event_candidates_batch_index",
        ),
        Index(
            "ix_story_event_candidates_instance_status",
            "production_recipe_instance_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "status IN ('candidate', 'selected', 'superseded')",
            name="ck_story_event_candidates_status",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    production_recipe_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_recipe_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_briefs.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="candidate", server_default="candidate"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    premise: Mapped[str] = mapped_column(Text, nullable=False)
    child_action: Mapped[str] = mapped_column(Text, nullable=False)
    cat_participation: Mapped[str] = mapped_column(Text, nullable=False)
    small_change: Mapped[str] = mapped_column(Text, nullable=False)
    warm_ending: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_scenes_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    duration_fit_summary: Mapped[str] = mapped_column(Text, nullable=False)
    requires_scene_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cat_behavior_mode_suggestion: Mapped[str] = mapped_column(String(40), nullable=False)
    score_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    generation_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL")
    )
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoryRevisionRecord(Base):
    __tablename__ = "story_revisions"
    __table_args__ = (
        UniqueConstraint("production_run_id", "revision", name="uq_story_revisions_run_revision"),
        Index("ix_story_revisions_run_status", "production_run_id", "status", "revision"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    brief_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.story_briefs.id", ondelete="SET NULL")
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_revisions.id", ondelete="SET NULL"),
    )
    source_event_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_event_candidates.id", ondelete="SET NULL"),
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="candidate", server_default="candidate"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    logline: Mapped[str] = mapped_column(Text, nullable=False)
    synopsis: Mapped[str] = mapped_column(Text, nullable=False)
    subject_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scene_plan_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    episode_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    candidate_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL")
    )
    critic_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoryboardRevision(Base):
    __tablename__ = "storyboard_revisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'structure_approved', 'production_approved', "
            "'changes_requested', 'superseded')",
            name="ck_storyboard_revisions_status",
        ),
        UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_storyboard_revisions_run_revision",
        ),
        Index(
            "ix_storyboard_revisions_current",
            "production_run_id",
            "status",
            "revision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    structure_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL"),
    )
    input_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    approved_structure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    production_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationPlan(Base):
    __tablename__ = "generation_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'approved', 'stale')",
            name="ck_generation_plans_status",
        ),
        UniqueConstraint(
            "storyboard_revision_id",
            "revision",
            name="uq_generation_plans_storyboard_revision",
        ),
        Index(
            "ix_generation_plans_current",
            "storyboard_revision_id",
            "status",
            "revision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    storyboard_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.storyboard_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="proposed", server_default="proposed"
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    capability_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_image_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    estimated_video_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    warnings_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    blockers_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoryScore(Base):
    __tablename__ = "story_scores"
    __table_args__ = (
        UniqueConstraint("story_revision_id", name="uq_story_scores_revision"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.story_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    opening_hook: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    causal_completeness: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    subject_necessity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    emotional_arc: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    visualizability: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    duration_fit: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    continuity_risk: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    safety: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SceneSubjectBinding(Base):
    __tablename__ = "scene_subject_bindings"
    __table_args__ = (
        UniqueConstraint("scene_id", "subject_revision_id", name="uq_scene_subject_binding"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    subject_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subject_revisions.id", ondelete="RESTRICT"),
    )
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    relationship_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class ShotBeat(Base):
    __tablename__ = "shot_beats"
    __table_args__ = (
        CheckConstraint(
            "cut_intent IN ('continuous', 'soft_cut', 'hard_cut')",
            name="ck_shot_beats_cut_intent",
        ),
        CheckConstraint(
            "reference_binding_revision >= 1",
            name="ck_shot_beats_reference_binding_revision",
        ),
        UniqueConstraint(
            "storyboard_revision_id",
            "scene_id",
            "sort_order",
            name="uq_shot_beats_storyboard_order",
        ),
        Index("ix_shot_beats_story", "story_revision_id", "scene_id", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    shot_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="SET NULL")
    )
    story_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.story_revisions.id", ondelete="SET NULL")
    )
    storyboard_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.storyboard_revisions.id", ondelete="CASCADE"),
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL")
    )
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reference_binding_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    visual_description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    child_action: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cat_action: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    spatial_relation: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    contact_occlusion: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    shot_size: Mapped[str] = mapped_column(
        String(200), nullable=False, default="中景", server_default="中景"
    )
    camera: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lighting: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dialogue: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sound_effect: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    music_intent: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    wardrobe_state: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    prop_state: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    continuity_in: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    continuity_out: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cut_intent: Mapped[str] = mapped_column(
        String(24), nullable=False, default="continuous", server_default="continuous"
    )
    duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    temporal_beats_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft"
    )
    stale_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationClipShot(Base):
    __tablename__ = "generation_clip_shots"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_generation_clip_shots_ordinal"),
        CheckConstraint(
            "start_second >= 0 AND end_second > start_second",
            name="ck_generation_clip_shots_interval",
        ),
        CheckConstraint(
            "transition_in IN ('continuous', 'soft_cut', 'hard_cut')",
            name="ck_generation_clip_shots_transition",
        ),
        UniqueConstraint(
            "generation_plan_id",
            "shot_card_id",
            "ordinal",
            name="uq_generation_clip_shots_clip_order",
        ),
        UniqueConstraint(
            "generation_plan_id",
            "shot_beat_id",
            name="uq_generation_clip_shots_plan_beat",
        ),
        Index(
            "ix_generation_clip_shots_plan_clip",
            "generation_plan_id",
            "shot_card_id",
            "ordinal",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generation_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    shot_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    shot_beat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.shot_beats.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_second: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_second: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    transition_in: Mapped[str] = mapped_column(
        String(24), nullable=False, default="continuous", server_default="continuous"
    )


class ShotSubjectState(Base):
    __tablename__ = "shot_subject_states"
    __table_args__ = (
        UniqueConstraint("shot_beat_id", "subject_revision_id", name="uq_shot_subject_state"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shot_beat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_beats.id", ondelete="CASCADE")
    )
    subject_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subject_revisions.id", ondelete="RESTRICT"),
    )
    start_state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    end_state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    interaction: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CanvasLayout(Base):
    __tablename__ = "canvas_layouts"
    __table_args__ = (
        UniqueConstraint("production_run_id", name="uq_canvas_layouts_run"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    viewport_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    operations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="saved", server_default="saved"
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    last_confirmed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_events.id", ondelete="SET NULL"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CanvasNodeArchive(Base):
    __tablename__ = "canvas_node_archives"
    __table_args__ = (
        UniqueConstraint(
            "production_run_id",
            "canvas_node_id",
            name="uq_canvas_node_archives_run_node",
        ),
        Index(
            "ix_canvas_node_archives_run_restored",
            "production_run_id",
            "restored_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    canvas_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    node_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderCapability(Base):
    __tablename__ = "provider_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "media_kind",
            name="uq_provider_capabilities_model_kind",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    media_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_generation_attempts_idempotency"),
        Index("ix_generation_attempts_object", "business_object_type", "business_object_id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL")
    )
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_attempts.id", ondelete="SET NULL"),
    )
    business_object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    business_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CanvasGraphNode(Base):
    __tablename__ = "canvas_graph_nodes"
    __table_args__ = (
        Index("ix_canvas_graph_nodes_run_type", "production_run_id", "node_type"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CanvasGraphEdge(Base):
    __tablename__ = "canvas_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_node_id",
            "source_port",
            "target_node_id",
            "target_port",
            name="uq_canvas_graph_edges_typed_connection",
        ),
        Index("ix_canvas_graph_edges_run", "production_run_id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_port: Mapped[str] = mapped_column(String(80), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_port: Mapped[str] = mapped_column(String(80), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanvasGroup(Base):
    __tablename__ = "canvas_groups"
    __table_args__ = (
        CheckConstraint("group_type IN ('recipe', 'shot')", name="ck_canvas_groups_type"),
        CheckConstraint(
            "lifecycle_status IN ('active', 'detached')",
            name="ck_canvas_groups_lifecycle",
        ),
        Index("ix_canvas_groups_run_status", "production_run_id", "lifecycle_status"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    production_recipe_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_recipe_instances.id", ondelete="SET NULL"),
    )
    parent_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_groups.id", ondelete="CASCADE"),
    )
    group_type: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#7c9cff")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    data_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CanvasGroupMember(Base):
    __tablename__ = "canvas_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "canvas_node_id", name="uq_canvas_group_members_node"),
        Index("ix_canvas_group_members_node", "canvas_node_id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    canvas_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanvasGroupTemplate(Base):
    __tablename__ = "canvas_group_templates"
    __table_args__ = ({"schema": SCHEMA_NAME},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanvasEvent(Base):
    __tablename__ = "canvas_events"
    __table_args__ = (
        Index("ix_canvas_events_run_created", "production_run_id", "created_at"),
        Index("ix_canvas_events_run_sequence", "production_run_id", "sequence"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        nullable=False,
        unique=True,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanvasRecoveryPoint(Base):
    __tablename__ = "canvas_recovery_points"
    __table_args__ = (
        Index(
            "ix_canvas_recovery_points_run_version",
            "production_run_id",
            "layout_version",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    layout_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.canvas_events.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubjectCompletionRun(Base):
    __tablename__ = "subject_completion_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_subject_completion_idempotency"),
        Index("ix_subject_completion_runs_subject_status", "subject_id", "status", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.subject_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL"),
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    missing_fields_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    proposal_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    accepted_fields_json: Mapped[list[str] | None] = mapped_column(JSONB)
    accepted_draft_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NodeGenerationConfig(Base):
    __tablename__ = "node_generation_configs"
    __table_args__ = (
        UniqueConstraint(
            "canvas_node_id", "revision", name="uq_node_generation_configs_revision"
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canvas_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actual_reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaGenerationBatch(Base):
    __tablename__ = "media_generation_batches"
    __table_args__ = (
        CheckConstraint("candidate_count BETWEEN 1 AND 8", name="ck_media_batches_candidates"),
        UniqueConstraint("idempotency_key", name="uq_media_batches_idempotency"),
        Index("ix_media_batches_run_status", "production_run_id", "status"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    canvas_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL")
    )
    media_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reference_manifest_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reference_manifest_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    output_asset_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class VideoEditRecipe(Base):
    __tablename__ = "video_edit_recipes"
    __table_args__ = (
        CheckConstraint("end_ms - start_ms BETWEEN 500 AND 13000", name="ck_video_edit_interval"),
        UniqueConstraint("canvas_node_id", "revision", name="uq_video_edit_node_revision"),
        Index("ix_video_edit_recipes_run_status", "production_run_id", "status"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    canvas_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id"), nullable=False
    )
    parent_recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.video_edit_recipes.id", ondelete="SET NULL")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    compilation_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class VideoEditAnnotation(Base):
    __tablename__ = "video_edit_annotations"
    __table_args__ = (
        UniqueConstraint("recipe_id", "ordinal", name="uq_video_edit_annotations_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.video_edit_recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(24), nullable=False)
    points_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")


class VideoEditReference(Base):
    __tablename__ = "video_edit_references"
    __table_args__ = (
        UniqueConstraint("recipe_id", "asset_id", name="uq_video_edit_reference_asset"),
        UniqueConstraint("recipe_id", "ordinal", name="uq_video_edit_reference_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.video_edit_recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_role: Mapped[str] = mapped_column(String(80), nullable=False, default="reference")
    provider_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CreatorProjectState(Base):
    __tablename__ = "creator_project_states"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_creator_project_states_version"),
        CheckConstraint(
            "target_duration_seconds BETWEEN 1 AND 360",
            name="ck_creator_project_states_duration",
        ),
        CheckConstraint(
            "aspect_ratio IN ('9:16', '16:9', '1:1')",
            name="ck_creator_project_states_aspect_ratio",
        ),
        CheckConstraint(
            "quality_tier IN ('quick', 'standard', 'quality')",
            name="ck_creator_project_states_quality_tier",
        ),
        {"schema": SCHEMA_NAME},
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    brief_body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    story_candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    current_story_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    target_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CreatorShot(Base):
    __tablename__ = "creator_shots"
    __table_args__ = (
        CheckConstraint("sort_order BETWEEN 1 AND 6", name="ck_creator_shots_order"),
        CheckConstraint("version >= 1", name="ck_creator_shots_version"),
        CheckConstraint("duration_seconds BETWEEN 1 AND 60", name="ck_creator_shots_duration"),
        UniqueConstraint("project_id", "sort_order", name="uq_creator_shots_project_order"),
        Index("ix_creator_shots_project", "project_id", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scene_label: Mapped[str | None] = mapped_column(String(160))
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    prompt_draft: Mapped[str | None] = mapped_column(Text)
    selected_video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL", use_alter=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GenerationSnapshot(Base):
    __tablename__ = "generation_snapshots"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('story_text', 'image', 'video', 'video_edit', 'composition')",
            name="ck_generation_snapshots_kind",
        ),
        CheckConstraint(
            "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
            name="ck_generation_snapshots_cost",
        ),
        Index("ix_generation_snapshots_project_created", "project_id", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    ordered_references_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    provider_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('canon', 'project', 'scene', 'shot', 'canvas_node')",
            name="ck_assets_scope",
        ),
        CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'ready', 'stale')",
            name="ck_assets_status",
        ),
        Index("ix_assets_sha256_role", "sha256", "role"),
        Index("ix_assets_shot_role", "shot_card_id", "role", "created_at"),
        Index("ix_assets_semantic_selection", "scope", "semantic_key", "status", "created_at"),
        Index("ix_assets_canvas_history", "production_run_id", "media_type", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE")
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    shot_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="CASCADE")
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="SET NULL"),
    )
    generation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_snapshots.id", ondelete="SET NULL"),
    )
    producing_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL")
    )
    canvas_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canvas_graph_nodes.id", ondelete="SET NULL"),
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoSequence(Base):
    __tablename__ = "video_sequences"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_video_sequences_revision"),
        CheckConstraint(
            "status IN ('content_review', 'approved', 'rejected')",
            name="ck_video_sequences_status",
        ),
        CheckConstraint("duration_ms > 0", name="ck_video_sequences_duration"),
        CheckConstraint("audio_policy = 'native_fades'", name="ck_video_sequences_audio_policy"),
        UniqueConstraint("production_run_id", "revision", name="uq_video_sequences_run_revision"),
        Index("ix_video_sequences_run_status", "production_run_id", "status", "revision"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.video_sequences.id", ondelete="SET NULL")
    )
    rendered_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="native_fades", server_default="native_fades"
    )
    clips_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("source IN ('human', 'ark_visual', 'technical')", name="ck_reviews_source"),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')", name="ck_reviews_decision"
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
