from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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

SCHEMA_NAME = "catflow"


class Base(DeclarativeBase):
    pass


class CanonProfileRecord(Base):
    __tablename__ = "canon_profiles"
    __table_args__ = (
        UniqueConstraint("profile_key", "version", name="uq_canon_profiles_key_version"),
        Index(
            "uq_canon_profiles_active",
            "profile_key",
            unique=True,
            postgresql_where=text("active = true"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectCollectionRecord(Base):
    __tablename__ = "project_collections"
    __table_args__ = (
        Index(
            "uq_project_collections_active_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_project_collections_sort", "sort_order", "name"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(40), nullable=False)
    color_key: Mapped[str] = mapped_column(String(16), nullable=False, default="clay")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProjectRecord(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("target_duration_seconds BETWEEN 8 AND 15", name="ck_projects_duration"),
        CheckConstraint("aspect_ratio = '9:16'", name="ck_projects_aspect_ratio"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False, default="9:16")
    canon_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.canon_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.project_collections.id", ondelete="SET NULL"),
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProjectTagRecord(Base):
    __tablename__ = "project_tags"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "normalized_name", name="uq_project_tags_project_normalized"
        ),
        Index("ix_project_tags_normalized", "normalized_name"),
        {"schema": SCHEMA_NAME},
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(24), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ValidationRunRecord(Base):
    __tablename__ = "validation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','authorized','paused','completed','cancelled')",
            name="ck_validation_runs_status",
        ),
        CheckConstraint("duration_seconds = 12", name="ck_validation_runs_duration"),
        CheckConstraint("resolution = '480p'", name="ck_validation_runs_resolution"),
        CheckConstraint("aspect_ratio = '9:16'", name="ck_validation_runs_aspect_ratio"),
        CheckConstraint(
            "status = 'cancelled' OR canon_snapshot_json IS NOT NULL",
            name="ck_validation_runs_canon_snapshot",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    topics_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    target_budget_cny: Mapped[int] = mapped_column(Integer, nullable=False)
    call_limits_json: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    usage_json: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    models_json: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    capability_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    cost_estimate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    canon_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    repair_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('plan_story','plan_shots','generate_image','diagnose_image',"
            "'generate_video','diagnose_video','regenerate_video_segment','render_export')",
            name="ck_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('queued','submitting','submitted','polling','storing','succeeded',"
            "'failed','cancel_requested','cancelled','submission_unknown')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "expected_cost_micros IS NULL OR expected_cost_micros >= 0", name="ck_jobs_cost"
        ),
        Index("ix_jobs_queue", "status", "created_at"),
        Index("ix_jobs_provider_task", "provider", "provider_task_id"),
        Index(
            "uq_jobs_validation_project_kind",
            "validation_run_id",
            "project_id",
            "kind",
            unique=True,
            postgresql_where=text("validation_run_id IS NOT NULL"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.validation_runs.id", ondelete="RESTRICT"),
    )
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="SET NULL")
    )
    video_repair_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.video_repairs.id", ondelete="SET NULL"),
    )
    provider_submission_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expected_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    billing_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    rate_card_revision: Mapped[str | None] = mapped_column(String(80))
    pricing_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    frozen_input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    supersedes_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="SET NULL")
    )
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AssetRecord(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("media_type IN ('image','video','audio')", name="ck_assets_media_type"),
        UniqueConstraint(
            "producing_job_id",
            "role",
            "candidate_index",
            name="uq_assets_job_role_candidate",
        ),
        Index(
            "uq_assets_project_sha_role_unproduced",
            "project_id",
            "sha256",
            "role",
            unique=True,
            postgresql_where=text("producing_job_id IS NULL AND project_id IS NOT NULL"),
        ),
        Index(
            "uq_assets_global_sha_role_unproduced",
            "sha256",
            "role",
            unique=True,
            postgresql_where=text("producing_job_id IS NULL AND project_id IS NULL"),
        ),
        Index("ix_assets_project_role", "project_id", "role", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    canon_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.canon_profiles.id", ondelete="SET NULL")
    )
    producing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="SET NULL")
    )
    candidate_index: Mapped[int | None] = mapped_column(SmallInteger)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaPublicationRecord(Base):
    __tablename__ = "media_publications"
    __table_args__ = (
        CheckConstraint(
            "state IN ('uploading','ready','delete_pending','deleted','failed')",
            name="ck_media_publications_state",
        ),
        Index("ix_media_publications_cleanup", "state", "delete_after"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    public_host: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_url_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectSelectionRecord(Base):
    __tablename__ = "project_selections"
    __table_args__ = (
        CheckConstraint(
            "slot IN ('episode_child','episode_cat','pair_scale','environment','style_board',"
            "'video','final')",
            name="ck_project_selections_slot",
        ),
        CheckConstraint(
            "decision IN ('selected','rejected','approved')", name="ck_project_selections_decision"
        ),
        Index("ix_project_selections_current", "project_id", "slot", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="RESTRICT")
    )
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LifePlannerSessionRecord(Base):
    __tablename__ = "life_planner_sessions"
    __table_args__ = ({"schema": SCHEMA_NAME},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LifePlannerMessageRecord(Base):
    __tablename__ = "life_planner_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_life_planner_messages_order"),
        CheckConstraint("role IN ('user','assistant')", name="ck_life_planner_messages_role"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.life_planner_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LifePlannerProposalRecord(Base):
    __tablename__ = "life_planner_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('draft','adopted','outdated')", name="ck_proposals_status"),
        Index("ix_proposals_project_status", "project_id", "status", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.life_planner_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryVersionRecord(Base):
    __tablename__ = "story_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_story_versions_revision"),
        Index(
            "uq_story_versions_active",
            "project_id",
            unique=True,
            postgresql_where=text("active = true"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.life_planner_proposals.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    micro_event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dialogue_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    environment_intent: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ShotPlanVersionRecord(Base):
    __tablename__ = "shot_plan_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_shot_plan_versions_revision"),
        CheckConstraint("total_duration_seconds BETWEEN 8 AND 15", name="ck_shot_plans_duration"),
        Index(
            "uq_shot_plan_versions_active",
            "project_id",
            unique=True,
            postgresql_where=text("active = true"),
        ),
        Index(
            "uq_shot_plan_versions_candidate",
            "project_id",
            unique=True,
            postgresql_where=text("review_status = 'candidate'"),
        ),
        CheckConstraint(
            "review_status IN ('accepted','candidate','rejected','superseded')",
            name="ck_shot_plan_versions_review_status",
        ),
        CheckConstraint(
            "NOT active OR review_status = 'accepted'",
            name="ck_shot_plan_versions_active_accepted",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_story_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.story_versions.id", ondelete="RESTRICT")
    )
    source_selection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    clip_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    shots_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    director_treatment_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    director_prompt_revision: Mapped[str | None] = mapped_column(String(80))
    director_model: Mapped[str | None] = mapped_column(String(120))
    director_input_hash: Mapped[str | None] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted")
    producing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="RESTRICT"),
        unique=True,
    )
    base_shot_plan_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.shot_plan_versions.id", ondelete="RESTRICT"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobEventRecord(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        Index("ix_job_events_project_id", "project_id", "id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.jobs.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EditVersionRecord(Base):
    __tablename__ = "edit_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_edit_versions_revision"),
        CheckConstraint(
            "status IN ('draft','rendered','approved')", name="ck_edit_versions_status"
        ),
        CheckConstraint("format_version IN (1,2)", name="ck_edit_versions_format_version"),
        Index(
            "uq_edit_versions_active",
            "project_id",
            unique=True,
            postgresql_where=text("active = true"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_selection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    edl_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    rendered_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL")
    )
    parent_edit_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.edit_versions.id", ondelete="SET NULL"),
    )
    format_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeline_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoRepairRecord(Base):
    __tablename__ = "video_repairs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','generating','candidate_ready','failed','approved','rejected',"
            "'outdated','cancelled')",
            name="ck_video_repairs_status",
        ),
        CheckConstraint(
            "issue_start_frame >= 0 AND issue_end_frame > issue_start_frame",
            name="ck_video_repairs_issue_range",
        ),
        CheckConstraint(
            "selection_policy_version IN (1, 2)",
            name="ck_video_repairs_selection_policy",
        ),
        CheckConstraint(
            "selection_policy_version = 1 OR "
            "issue_end_frame - issue_start_frame BETWEEN 96 AND 360",
            name="ck_video_repairs_v2_issue_duration",
        ),
        CheckConstraint(
            "generation_start_frame >= 0 AND generation_end_frame > generation_start_frame",
            name="ck_video_repairs_generation_range",
        ),
        CheckConstraint(
            "provider_duration_seconds BETWEEN 4 AND 15",
            name="ck_video_repairs_provider_duration",
        ),
        Index("ix_video_repairs_project_created", "project_id", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    base_video_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_edit_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.edit_versions.id", ondelete="RESTRICT"),
    )
    base_timeline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frame_rate_numerator: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_rate_denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_end_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_end_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_core_start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_core_end_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    selection_policy_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=2
    )
    edit_intent: Mapped[str | None] = mapped_column(String(24))
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL")
    )
    approved_candidate_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL")
    )
    approved_edit_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.edit_versions.id", ondelete="SET NULL"),
    )
    approval_idempotency_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    preview_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderRateCardRecord(Base):
    __tablename__ = "provider_rate_cards"
    __table_args__ = (
        CheckConstraint("unit_price_micros >= 0", name="ck_rate_cards_nonnegative_price"),
        CheckConstraint("currency = 'CNY'", name="ck_rate_cards_currency"),
        CheckConstraint(
            "unit IN ('million_tokens','image','video_second')", name="ck_rate_cards_unit"
        ),
        UniqueConstraint(
            "provider", "model", "metric", "revision", name="uq_provider_rate_card_revision"
        ),
        Index("ix_provider_rate_cards_active", "provider", "model", "active"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    source_url: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
