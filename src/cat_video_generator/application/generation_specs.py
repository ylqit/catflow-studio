"""Authoritative, non-persistent projections for one shot generation request."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from ..domain.contracts import ReferenceTarget
from ..domain.prompts import CompiledPrompt
from ..domain.rendering import VideoInputPlan
from .ports import (
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
)


class ProviderInputMode(StrEnum):
    """Mutually exclusive media modes exposed by the video provider."""

    TEXT_ONLY = "text_only"
    REFERENCE_MEDIA = "reference_media"
    FIRST_FRAME = "first_frame"
    FIRST_LAST_FRAME = "first_last_frame"


@dataclass(frozen=True, slots=True)
class ShotCompilationContext:
    """Batch-loaded state required to compile one shot without further reads."""

    project: StoredProject
    scene: StoredScene
    shot: StoredShot
    visual_profile: StoredVisualProfileRevision
    assets_by_id: Mapping[UUID, StoredAsset]
    shot_steps: tuple[StoredStep, ...]


@dataclass(frozen=True, slots=True)
class ShotGenerationSpec:
    """One projection shared by preview, board, workspace and paid submission."""

    target: ReferenceTarget
    provider_input_mode: ProviderInputMode
    prompt: CompiledPrompt
    creative_body: str
    system_shell: str
    input_plan: VideoInputPlan | None
    sources: tuple[StoredAsset, ...]
    descriptions: tuple[str, ...]
    snapshot: dict[str, Any]
    input_hash: str
    source_revision_hash: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def actual_input_count(self) -> int:
        return len(self.sources)
