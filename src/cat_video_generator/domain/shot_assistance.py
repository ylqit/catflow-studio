"""Zero-cost structural inspection for an editable video-clip draft.

Creative judgements such as physical feasibility, action density, camera motion,
sound and ending quality belong to the staged LLM review.  This module only
recognizes the numbered 2-to-4-subshot format without interpreting story words.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field

from .contract_base import StrictModel
from .contracts import ShotAssistPatch, ShotCardDraft

_SUBSHOT_PATTERN = re.compile(r"(?m)^\s*\d+\s*[.、．]")
class ShotRuleFinding(StrictModel):
    code: str
    severity: Literal["info", "warning"]
    message: str


class ShotLocalAnalysis(StrictModel):
    suggested_subshot_min: Annotated[int, Field(alias="suggestedSubshotMin", ge=2, le=4)]
    suggested_subshot_max: Annotated[int, Field(alias="suggestedSubshotMax", ge=2, le=4)]
    detected_subshot_count: Annotated[int, Field(alias="detectedSubshotCount", ge=1)]
    action_count: Annotated[int | None, Field(alias="actionCount", ge=0)] = None
    camera_move_count: Annotated[int | None, Field(alias="cameraMoveCount", ge=0)] = None
    has_stable_ending: bool | None = Field(default=None, alias="hasStableEnding")
    has_sound: bool | None = Field(default=None, alias="hasSound")
    qualitative_pacing: str = Field(alias="qualitativePacing")
    findings: list[ShotRuleFinding]


def analyze_shot_draft(draft: ShotCardDraft) -> ShotLocalAnalysis:
    suggested = (2, 4)
    detected_subshots = max(1, len(_SUBSHOT_PATTERN.findall(draft.direction)))
    findings: list[ShotRuleFinding] = []
    if detected_subshots < suggested[0]:
        findings.append(
            ShotRuleFinding(
                code="subshot_density_low",
                severity="warning",
                message="当前正文少于2个编号子镜头；创作合理性请使用LLM审稿判断。",
            )
        )
    elif detected_subshots > suggested[1]:
        findings.append(
            ShotRuleFinding(
                code="subshot_density_high",
                severity="warning",
                message="当前正文超过4个编号子镜头；是否拆分请使用LLM审稿判断。",
            )
        )
    return ShotLocalAnalysis(
        suggestedSubshotMin=suggested[0],
        suggestedSubshotMax=suggested[1],
        detectedSubshotCount=detected_subshots,
        actionCount=None,
        cameraMoveCount=None,
        hasStableEnding=None,
        hasSound=None,
        qualitativePacing="由视觉与Prompt审稿LLM结合剧情、素材和目标时长提出",
        findings=findings,
    )


def apply_shot_assist_patch(
    draft: ShotCardDraft,
    patch: ShotAssistPatch,
) -> ShotCardDraft:
    values = draft.model_dump(mode="python")
    selected = patch.model_dump(mode="python", exclude_none=True)
    values.update(selected)
    return ShotCardDraft.model_validate(values)
