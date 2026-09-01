from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    provider: str
    planning_model: str
    image_model: str
    video_model: str
    diagnostic_model: str
    capability_revision: str
    paid_calls_enabled: bool
    maximum_video_references: int

    @classmethod
    def from_env(cls) -> ProviderRuntime:
        provider = os.environ.get("CATFLOW_PROVIDER", "fake").strip().lower()
        if provider == "ark":
            return cls(
                provider="ark",
                planning_model=os.environ.get(
                    "ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"
                ),
                image_model=os.environ.get(
                    "ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"
                ),
                video_model=os.environ.get(
                    "ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"
                ),
                diagnostic_model=os.environ.get(
                    "ARK_DIAGNOSTIC_MODEL",
                    os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
                ),
                capability_revision="ark-seedance-2.0-v1",
                paid_calls_enabled=os.environ.get(
                    "CATFLOW_PAID_CALLS_ENABLED", "false"
                ).lower()
                == "true",
                maximum_video_references=5,
            )
        if provider != "fake":
            raise ValueError(f"unsupported CatFlow provider: {provider}")
        return cls.fake()

    @classmethod
    def fake(cls) -> ProviderRuntime:
        return cls(
            provider="fake",
            planning_model="catflow-fake-planner-v1",
            image_model="catflow-fake-image-v1",
            video_model="catflow-fake-video-v1",
            diagnostic_model="catflow-fake-diagnostic-v1",
            capability_revision="fake-v1",
            paid_calls_enabled=False,
            maximum_video_references=5,
        )
