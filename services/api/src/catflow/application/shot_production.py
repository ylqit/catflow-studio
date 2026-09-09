from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import Field

from catflow.application.job_execution import PaidJobCommand
from catflow.domain.contract import ContractModel
from catflow.domain.models import ShotSpec


class ShotTarget(ContractModel):
    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    shot_id: str = Field(alias="shotId", min_length=1, max_length=80)


class ShotMediaPreviewCommand(ShotTarget):
    purpose: Literal["shot_frame", "shot_video"]


class ShotMediaCommand(ShotMediaPreviewCommand, PaidJobCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotFrameConfirmCommand(ShotTarget):
    asset_id: uuid.UUID = Field(alias="assetId")
    expected_design_hash: str = Field(alias="expectedDesignHash", pattern=r"^[a-f0-9]{64}$")
    checks: list[
        Literal["identity_scale", "placement_state", "movement_space", "action_start", "continuity"]
    ] = Field(min_length=5, max_length=5)


class ShotFrameExtractCommand(ShotTarget):
    source_video_asset_id: uuid.UUID = Field(alias="sourceVideoAssetId")
    frame: int = Field(ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotTake(ContractModel):
    shot_id: str = Field(alias="shotId", min_length=1)
    asset_id: uuid.UUID = Field(alias="assetId")
    source_in_frame: int = Field(alias="sourceInFrame", ge=0)


class ShotAssemblyCommand(ContractModel):
    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    takes: list[ShotTake] = Field(min_length=1, max_length=4)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


def shot_design_hash(shot: ShotSpec, references: list[dict], environment_intent: str) -> str:
    # Confirmation is deliberately excluded: selecting a frame must not invalidate itself.
    document = {
        "shot": shot.model_dump(mode="json", by_alias=True, exclude={"confirmed_frame"}),
        "references": references,
        "environmentIntent": environment_intent,
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
