from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class ValidationCallKind(StrEnum):
    PLAN_STORY = "plan_story"
    GENERATE_IMAGE = "generate_image"
    DIAGNOSE_IMAGE = "diagnose_image"
    GENERATE_VIDEO = "generate_video"
    DIAGNOSE_VIDEO = "diagnose_video"


class ValidationLimitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationManifest:
    topics: tuple[str, ...]
    duration_seconds: int
    resolution: str
    aspect_ratio: str
    target_budget_cny: int
    call_limits: Mapping[ValidationCallKind, int]

    @property
    def total_call_limit(self) -> int:
        return sum(self.call_limits.values())


def first_release_manifest() -> ValidationManifest:
    return ValidationManifest(
        topics=("雨天擦爪", "浇花", "寻找滚落线团"),
        duration_seconds=12,
        resolution="480p",
        aspect_ratio="9:16",
        target_budget_cny=50,
        call_limits=MappingProxyType(
            {
                ValidationCallKind.PLAN_STORY: 3,
                ValidationCallKind.GENERATE_IMAGE: 1,
                ValidationCallKind.DIAGNOSE_IMAGE: 1,
                ValidationCallKind.GENERATE_VIDEO: 3,
                ValidationCallKind.DIAGNOSE_VIDEO: 1,
            }
        ),
    )


def reserve_validation_call(
    manifest: ValidationManifest,
    usage: Mapping[ValidationCallKind, int],
    kind: ValidationCallKind,
) -> dict[ValidationCallKind, int]:
    if sum(usage.values()) >= manifest.total_call_limit:
        raise ValidationLimitError("validation run reached its total paid-call limit")
    if usage.get(kind, 0) >= manifest.call_limits[kind]:
        raise ValidationLimitError(f"validation run reached its {kind.value} limit")
    reserved = dict(usage)
    reserved[kind] = reserved.get(kind, 0) + 1
    return reserved
