from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .models import DirectorPlanPayload

DirectorResultDisposition = Literal["candidate_ready", "needs_input", "invalid"]
DirectorValidationSeverity = Literal["fatal", "blocking", "warning"]


@dataclass(frozen=True, slots=True)
class DirectorValidationIssue:
    code: str
    severity: DirectorValidationSeverity
    path: str
    message: str
    suggested_action: str | None = None
    provider_value: object | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.suggested_action:
            value["suggestedAction"] = self.suggested_action
        if self.provider_value is not None:
            value["providerValue"] = self.provider_value
        return value


@dataclass(frozen=True, slots=True)
class DirectorNormalizationResult:
    raw_payload: dict[str, object]
    normalized_payload: dict[str, object] | None
    disposition: DirectorResultDisposition
    issues: tuple[DirectorValidationIssue, ...]
    plan: DirectorPlanPayload | None = None

    @property
    def recoverable(self) -> bool:
        return self.disposition != "invalid" and self.normalized_payload is not None

    def validation_document(self) -> dict[str, object]:
        value: dict[str, object] = {
            "disposition": self.disposition,
            "recoverable": self.recoverable,
            "issues": [issue.as_dict() for issue in self.issues],
        }
        if self.normalized_payload is not None:
            value["normalizedPayload"] = self.normalized_payload
        return value


_CORE_SHOT_FIELDS = (
    "framing",
    "cameraMovement",
    "childAction",
    "catAction",
    "environmentChange",
)
_SOUND_LIST_FIELDS = ("ambience", "objectEffects", "movementEffects")
_CREATIVE_LIST_FIELDS = {
    "ambience",
    "objectEffects",
    "movementEffects",
    "microMotions",
    "generationRisks",
    "feasibilityWarnings",
}
_REQUIRED_PROFESSIONAL_FIELDS = (
    "durationFrames",
    "lens",
    "composition",
    "childBlocking",
    "catBlocking",
    "physicalChange",
    "continuity",
    "lighting",
    "sound",
    "directorIntent",
)


def _path_text(location: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in location)


def _remove_path(value: object, location: tuple[str | int, ...]) -> bool:
    if not location:
        return False
    current = value
    for part in location[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                return False
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
    final = location[-1]
    if isinstance(final, int):
        if not isinstance(current, list) or final < 0 or final >= len(current):
            return False
        del current[final]
        return True
    if not isinstance(current, dict) or final not in current:
        return False
    del current[final]
    return True


def _read_path(value: object, location: tuple[str | int, ...]) -> object | None:
    current = value
    for part in location:
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return deepcopy(current)


def _zero_duration_empty_placeholder(shot: dict[str, object]) -> bool:
    duration_seconds = shot.get("durationSeconds")
    duration_frames = shot.get("durationFrames")
    zero_duration = duration_seconds == 0 and duration_frames in (None, 0)
    core_is_empty = all(not str(shot.get(field, "")).strip() for field in _CORE_SHOT_FIELDS)
    return zero_duration and core_is_empty


def _normalize_string_list(
    container: dict[str, object],
    field: str,
    *,
    path: str,
    issues: list[DirectorValidationIssue],
) -> None:
    value = container.get(field)
    if isinstance(value, str):
        container[field] = [value]
        issues.append(
            DirectorValidationIssue(
                code="single_string_normalized",
                severity="warning",
                path=f"{path}.{field}",
                message="模型返回了单条文本，已安全转换为列表。",
            )
        )


def _creative_density_issues(
    shot: dict[str, object], shot_index: int
) -> list[DirectorValidationIssue]:
    issues: list[DirectorValidationIssue] = []
    sound = shot.get("sound")
    if isinstance(sound, dict):
        for field in _SOUND_LIST_FIELDS:
            _normalize_string_list(
                sound,
                field,
                path=f"shots.{shot_index}.sound",
                issues=issues,
            )
            values = sound.get(field)
            if isinstance(values, list) and len(values) > 3:
                issues.append(
                    DirectorValidationIssue(
                        code="sound_detail_dense",
                        severity="warning",
                        path=f"shots.{shot_index}.sound.{field}",
                        message=f"该镜头的声音细节较多（{len(values)} 条），已全部保留。",
                        suggested_action="可以直接采用；若生成执行不稳定，可在采用前主动精简。",
                    )
                )
    for role_field in ("childBlocking", "catBlocking"):
        blocking = shot.get(role_field)
        if not isinstance(blocking, dict):
            continue
        _normalize_string_list(
            blocking,
            "microMotions",
            path=f"shots.{shot_index}.{role_field}",
            issues=issues,
        )
        micro_motions = blocking.get("microMotions")
        if isinstance(micro_motions, list) and len(micro_motions) > 3:
            issues.append(
                DirectorValidationIssue(
                    code="micro_motion_dense",
                    severity="warning",
                    path=f"shots.{shot_index}.{role_field}.microMotions",
                    message="该镜头的微动作较多，生成模型可能难以同时准确执行。",
                    suggested_action="可以直接采用，或在采用前精简次要动作。",
                )
            )
    return issues


def _blocking_issue(error: dict[str, Any]) -> DirectorValidationIssue:
    location = tuple(error.get("loc", ()))
    error_type = str(error.get("type", "validation_error"))
    path = _path_text(location)
    if location == ("shots",) and error_type == "too_long":
        return DirectorValidationIssue(
            code="too_many_meaningful_shots",
            severity="blocking",
            path="shots",
            message="分镜包含超过 4 个有内容的镜头，需要在采用前精简。",
            suggested_action="保留 1–4 个有意义镜头，并确保总时长闭合。",
        )
    return DirectorValidationIssue(
        code="required_content_invalid",
        severity="blocking",
        path=path,
        message=str(error.get("msg", "该字段需要补充或修正。")),
        suggested_action="在分镜草稿中补充或修正此项后，再创建待确认版本。",
    )


def normalize_director_result(payload: object) -> DirectorNormalizationResult:
    """Normalize the untrusted Provider boundary without weakening saved ShotPlan DTOs."""

    if not isinstance(payload, dict):
        return DirectorNormalizationResult(
            raw_payload={},
            normalized_payload=None,
            disposition="invalid",
            issues=(
                DirectorValidationIssue(
                    code="invalid_root",
                    severity="fatal",
                    path="",
                    message="模型结果不是可读取的 JSON 对象。",
                ),
            ),
        )

    raw_payload = deepcopy(payload)
    normalized: dict[str, object] = deepcopy(payload)
    shots = normalized.get("shots")
    if not isinstance(shots, list):
        return DirectorNormalizationResult(
            raw_payload=raw_payload,
            normalized_payload=None,
            disposition="invalid",
            issues=(
                DirectorValidationIssue(
                    code="shots_not_array",
                    severity="fatal",
                    path="shots",
                    message="模型结果缺少可读取的镜头列表。",
                ),
            ),
        )

    issues: list[DirectorValidationIssue] = []
    meaningful_shots: list[object] = []
    for original_index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            return DirectorNormalizationResult(
                raw_payload=raw_payload,
                normalized_payload=normalized,
                disposition="invalid",
                issues=(
                    DirectorValidationIssue(
                        code="shot_not_object",
                        severity="fatal",
                        path=f"shots.{original_index}",
                        message="镜头条目不是可读取的对象。",
                    ),
                ),
            )
        if _zero_duration_empty_placeholder(shot):
            issues.append(
                DirectorValidationIssue(
                    code="empty_placeholder_ignored",
                    severity="warning",
                    path=f"shots.{original_index}",
                    message="模型附带了一个空的零时长占位镜头，已忽略。",
                )
            )
            continue
        meaningful_shots.append(shot)

    if not meaningful_shots:
        return DirectorNormalizationResult(
            raw_payload=raw_payload,
            normalized_payload=normalized,
            disposition="invalid",
            issues=tuple(
                issues
                + [
                    DirectorValidationIssue(
                        code="no_meaningful_shots",
                        severity="fatal",
                        path="shots",
                        message="模型没有返回有内容的镜头。",
                    )
                ]
            ),
        )

    normalized["shots"] = meaningful_shots
    for shot_index, shot in enumerate(meaningful_shots):
        assert isinstance(shot, dict)
        issues.extend(_creative_density_issues(shot, shot_index))
        for field in _REQUIRED_PROFESSIONAL_FIELDS:
            if shot.get(field) is None:
                issues.append(
                    DirectorValidationIssue(
                        code="required_content_missing",
                        severity="blocking",
                        path=f"shots.{shot_index}.{field}",
                        message="该项是采用分镜前必须补充的内容。",
                        suggested_action="补充此项后，从已有结果创建待确认版本。",
                    )
                )

    # Extra Provider fields are evidence, not canonical ShotSpec fields. Remove only
    # fields Pydantic identifies as extras and retry; every other validation error is
    # retained as an adoption-blocking issue.
    while True:
        try:
            plan = DirectorPlanPayload.model_validate(normalized)
            return DirectorNormalizationResult(
                raw_payload=raw_payload,
                normalized_payload=normalized,
                disposition="candidate_ready",
                issues=tuple(issues),
                plan=plan,
            )
        except ValidationError as exc:
            extra_errors = [
                error
                for error in exc.errors(include_url=False)
                if error["type"] == "extra_forbidden"
            ]
            removed_any = False
            for error in extra_errors:
                location = tuple(error["loc"])
                provider_value = _read_path(normalized, location)
                if _remove_path(normalized, location):
                    removed_any = True
                    issues.append(
                        DirectorValidationIssue(
                            code="unknown_provider_field",
                            severity="warning",
                            path=_path_text(location),
                            message="模型附带了一项额外说明，已保存在生成记录中。",
                            provider_value=provider_value,
                        )
                    )
            if removed_any:
                continue
            blocking = tuple(_blocking_issue(error) for error in exc.errors(include_url=False))
            return DirectorNormalizationResult(
                raw_payload=raw_payload,
                normalized_payload=normalized,
                disposition="needs_input",
                issues=tuple(issues) + blocking,
            )


def director_provider_output_schema() -> dict[str, object]:
    """Describe parseable Provider output without encoding creative-density limits."""

    schema: dict[str, object] = deepcopy(DirectorPlanPayload.model_json_schema(by_alias=True))

    def visit(node: object, field_name: str | None = None) -> None:
        if isinstance(node, dict):
            if field_name in _CREATIVE_LIST_FIELDS:
                node.pop("maxItems", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                for name, child in properties.items():
                    visit(child, str(name))
            for name, child in node.items():
                if name != "properties":
                    visit(child, field_name)
        elif isinstance(node, list):
            for child in node:
                visit(child, field_name)

    visit(schema)
    return schema
