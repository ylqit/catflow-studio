"""Persistence for immutable production plans and adopted unit evidence."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, SCHEMA_NAME


class ProductionPlanRecord(Base):
    __tablename__ = "production_plan_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_production_plan_revision"),
        UniqueConstraint("idempotency_key", name="uq_production_plan_idempotency"),
        Index("uq_production_plan_active", "project_id", unique=True,
              postgresql_where=text("active = true")),
        {"schema": SCHEMA_NAME},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="RESTRICT"))
    shot_plan_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.shot_plan_versions.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    input_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(96))
    document_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionSelectionRecord(Base):
    __tablename__ = "production_unit_selections"
    __table_args__ = (
        UniqueConstraint("project_id", "unit_id", "revision", name="uq_unit_selection_revision"),
        UniqueConstraint("idempotency_key", name="uq_unit_selection_idempotency"),
        Index("uq_unit_selection_active", "project_id", "unit_id", unique=True,
              postgresql_where=text("active = true")),
        {"schema": SCHEMA_NAME},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.projects.id", ondelete="RESTRICT"))
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.production_plan_versions.id", ondelete="RESTRICT"))
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="RESTRICT"))
    unit_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(96))
    document_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
