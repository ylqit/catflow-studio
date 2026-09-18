"""导演/分镜 LLM 输出的接收、契约校验与确定性归一化。

当前输出契约: professional-director-v4-performance;
当前归一化版本: director-normalizer-v4-performance(legacy 历史重解释固定为 director-normalizer-v1)。
两者随校验文档一并持久化,用于区分不同年代结果的解释方式;历史契约
(v2 DirectorPlanPayload / v3 ProfessionalDirectorOutput)保留兼容路径,
读取旧任务结果时不改变当年的解释。

disposition(结果处置结论)语义:
- candidate_ready: 结构可读且没有 fatal/blocking 问题,可直接创建待确认分镜版本
- needs_input:     结果可读,但存在必须人工补充/修正的问题,采用前需创作者处理
- invalid:         结果不可读(根不是对象/缺少镜头列表/全是空占位),只能走显式修复

severity(单条问题严重级)语义:
- fatal:    结果整体不可用,无法从结果本身恢复
- blocking: 具体字段需要补充或修正,阻断自动采用(disposition 降为 needs_input)
- warning:  确定性整理或人工复核提示(如单字符串转列表、额外字段留档、结尾复核),
            只提示、不阻断;问题全部为 warning 时结果仍是 candidate_ready
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .models import DirectorPlanPayload, PerformanceDirectorOutput, ProfessionalDirectorOutput

# 当前导演输出契约与归一化算法版本(语义见模块 docstring)
DIRECTOR_OUTPUT_CONTRACT = "professional-director-v4-performance"
DIRECTOR_NORMALIZATION_REVISION = "director-normalizer-v4-performance"

DirectorResultDisposition = Literal["candidate_ready", "needs_input", "invalid"]
DirectorValidationSeverity = Literal["fatal", "blocking", "warning"]


def completed_director_text(provider_result: dict[str, Any] | None) -> str | None:
    """只在 Responses 状态为 completed 时暴露完整存储正文,供显式修复使用;绝不读取流式检查点。

    正文取自 output 中 message/output_text 片段的拼接(片段为空时退回顶层 output_text 字段);
    为空白或超过 2MiB 上限时返回 None,避免异常巨大的输出进入修复界面。
    """
    response = (provider_result or {}).get("rawResponse")
    # 只有 completed 状态的存储响应才可信;流式中间检查点可能不完整,直接拒绝
    if not isinstance(response, dict) or response.get("status") != "completed":
        return None
    parts = [
        content["text"]
        for item in response.get("output", [])
        if isinstance(item, dict) and item.get("type") == "message"
        for content in item.get("content", [])
        if isinstance(content, dict)
        and content.get("type") == "output_text"
        and isinstance(content.get("text"), str)
    ]
    text = "".join(parts) or response.get("output_text")
    return (
        text
        if isinstance(text, str) and text.strip() and len(text.encode()) <= 2 * 1024 * 1024
        else None
    )


@dataclass(frozen=True, slots=True)
class DirectorValidationIssue:
    """单条导演结果校验问题 —— 严重级 + JSON 路径 + 面向创作者的中文说明。"""

    code: str
    severity: DirectorValidationSeverity
    path: str
    message: str
    suggested_action: str | None = None  # 可选的下一步操作建议(展示给创作者)
    provider_value: object | None = None  # 模型原值留档(如被移除的额外字段、冲突的两处原文)

    def as_dict(self) -> dict[str, object]:
        """转换为 API/持久化文档格式(camelCase;可选字段为空时省略)。"""
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
    """导演结果归一化产物 —— 原始/归一化 payload、处置结论与全部校验问题。"""

    raw_payload: dict[str, object]  # 模型原始输出深拷贝(不做任何修改,留作证据)
    normalized_payload: dict[str, object] | None  # 确定性整理后的副本;整体不可读时为 None
    disposition: DirectorResultDisposition
    issues: tuple[DirectorValidationIssue, ...]
    plan: DirectorPlanPayload | None = None  # 仅 candidate_ready 时携带已通过契约校验的计划
    normalization_revision: str = DIRECTOR_NORMALIZATION_REVISION

    @property
    def recoverable(self) -> bool:
        """是否可恢复:结果仍可读取(非 invalid)且已有归一化 payload。"""
        return self.disposition != "invalid" and self.normalized_payload is not None

    def validation_document(self) -> dict[str, object]:
        """生成可持久化的校验文档 —— 存入 job.provider_result["validation"],读取历史时原样保留。

        adjustments 只收录"确定性整理"类问题(路径归一化/单字符串转列表/占位镜头忽略),
        供前端区分"系统已自动整理"与"需要人工处理"的问题。
        """
        value: dict[str, object] = {
            "disposition": self.disposition,
            "recoverable": self.recoverable,
            "issues": [issue.as_dict() for issue in self.issues],
            "normalizationRevision": self.normalization_revision,
            # 原始 payload 的规范化 JSON SHA256:审计锚点,证明校验针对的正是这份模型输出
            "rawPayloadHash": hashlib.sha256(
                json.dumps(
                    self.raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "adjustments": [
                issue.as_dict()
                for issue in self.issues
                if issue.code
                in (
                    "blocking_path_normalized",
                    "blocking_duplicate_normalized",
                    "single_string_normalized",
                    "empty_placeholder_ignored",
                )
            ],
        }
        if self.normalized_payload is not None:
            value["normalizedPayload"] = self.normalized_payload
        return value


# 判定"空占位镜头"用的核心内容字段:全部空白且零时长才视为占位
_CORE_SHOT_FIELDS = (
    "framing",
    "cameraMovement",
    "childAction",
    "catAction",
    "environmentChange",
)
# 需要"单字符串 → 单元素列表"安全归一化的声音字段
_SOUND_LIST_FIELDS = ("ambience", "objectEffects", "movementEffects")
# 创意密度相关的列表字段:对外输出 schema 时会移除其 maxItems
# (密度只作为 warning 提示创作者,不写进契约限制模型发挥)
_CREATIVE_LIST_FIELDS = {
    "ambience",
    "objectEffects",
    "movementEffects",
    "microMotions",
    "generationRisks",
    "feasibilityWarnings",
}
# v4 表演契约要求每个镜头补齐的专业字段;legacy 旧结果缺失时逐项报 blocking 提示补充
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
    """把 pydantic 错误位置元组转为点分路径文本(如 ("shots", 0) → "shots.0")。"""
    return ".".join(str(part) for part in location)


def _remove_path(value: object, location: tuple[str | int, ...]) -> bool:
    """按位置元组就地删除嵌套字段;路径不存在或中途类型不符时返回 False,不抛错。"""
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
    """按位置元组深拷贝读取嵌套字段值(用于把模型原值留档);读不到返回 None。"""
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
    """判断是否为模型附带的空占位镜头:零时长(秒=0 且帧缺省或 0)且核心内容字段全部空白。"""
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
    """把误写成单条字符串的列表字段安全包装为 [字符串];内容不改,并记一条 warning。"""
    value = container.get(field)
    if isinstance(value, str):
        # 确定性整理:单字符串 → 单元素列表,模型原意完整保留
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
    """创意密度检查 —— 声音细节/微动作超过 3 条时记 warning 提示可精简。

    全部条目原样保留:密度高不是错误,是否精简由创作者决定;
    顺带对声音字段与 microMotions 做"单字符串 → 列表"归一化。
    """
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
    """把一条 pydantic ValidationError 转成 blocking 级校验问题(采用前必须人工处理)。"""
    location = tuple(error.get("loc", ()))
    error_type = str(error.get("type", "validation_error"))
    path = _path_text(location)
    # 镜头数超过契约上限(1–4 个有内容镜头)时,给专门的可操作提示
    if location == ("shots",) and error_type == "too_long":
        return DirectorValidationIssue(
            code="too_many_meaningful_shots",
            severity="blocking",
            path="shots",
            message="分镜包含超过 4 个有内容的镜头，需要在采用前精简。",
            suggested_action="保留 1–4 个有意义镜头，并确保总时长闭合。",
        )
    message = str(error.get("msg", "该字段需要补充或修正。"))
    if error_type == "value_error" and message.startswith("Value error, "):
        # 契约校验器已改为抛出中文 ValueError;去掉 pydantic 的英文包装前缀,
        # 让创作者看到纯净的中文说明。
        message = message.removeprefix("Value error, ")
    return DirectorValidationIssue(
        code="required_content_invalid",
        severity="blocking",
        path=path,
        message=message,
        suggested_action="在分镜草稿中补充或修正此项后，再创建待确认版本。",
    )


def _normalize_blocking_paths(
    shot: dict[str, object], shot_index: int
) -> list[DirectorValidationIssue]:
    """归一化已观测到的 blocking 别名路径。

    模型偶尔把 childBlocking/catBlocking 嵌进 blocking 子对象;只有这种已观测到的
    别名可以安全搬移,绝不猜测角色名或改写内容。
    """
    container = shot.get("blocking")
    if not isinstance(container, dict):
        return []
    issues: list[DirectorValidationIssue] = []
    for field in ("childBlocking", "catBlocking"):
        if field not in container:
            continue
        value = container[field]
        canonical = shot.get(field)
        source = f"shots.{shot_index}.blocking.{field}"
        target = f"shots.{shot_index}.{field}"
        # 规范位置为空、或两处内容完全一致 → 安全搬移,只记 warning(内容未改动);
        # code 区分 blocking_path_normalized(搬移)与 blocking_duplicate_normalized(重复值去重)
        if isinstance(value, dict) and (canonical is None or canonical == value):
            shot[field] = value
            del container[field]
            issues.append(
                DirectorValidationIssue(
                    code=(
                        "blocking_path_normalized"
                        if canonical is None
                        else "blocking_duplicate_normalized"
                    ),
                    severity="warning",
                    path=target,
                    message="已整理字段位置，内容未改动。",
                    provider_value={"sourcePath": source, "targetPath": target},
                )
            )
        else:
            # 两处内容不一致或类型错误 → 不自动仲裁;记 blocking 并把两处原文都留档,
            # 由创作者明确保留哪一份
            issues.append(
                DirectorValidationIssue(
                    code="blocking_path_conflict",
                    severity="blocking",
                    path=target,
                    message="动作字段存在冲突或类型错误，请核对两处原文后明确修订。",
                    provider_value={"canonical": deepcopy(canonical), "nested": deepcopy(value)},
                    suggested_action="在动作表单中明确保留的内容；两处原文保留在此记录中。",
                )
            )
    # blocking 掏空后整体删除,避免残留为契约外的额外字段
    if not container:
        del shot["blocking"]
    return issues


def normalize_director_result(
    payload: object,
    *,
    legacy: bool = False,
    output_contract_revision: str = DIRECTOR_OUTPUT_CONTRACT,
) -> DirectorNormalizationResult:
    """在不放松已保存 ShotPlan DTO 的前提下,归一化不可信的 Provider 边界输出。

    流程:根类型检查 → 镜头列表检查 → 剔除空占位镜头 → 逐镜整理(blocking 别名归位与
    创意密度检查;legacy 只报缺失的专业字段)→ 按契约版本用对应 DTO 做 pydantic 校验;
    校验失败时仅移除 extra_forbidden 指认的额外字段(原值留档为 warning)后重试,
    其余错误全部保留为阻断采用的 blocking 问题。

    Args:
        payload: 模型返回的不可信对象(通常来自 job.provider_result["payload"])
        legacy: True 表示对旧任务结果做历史兼容重解释 —— 用 v2 基础契约、
            不做 blocking 路径整理,归一化版本固定为 director-normalizer-v1
        output_contract_revision: 结果生成时冻结的输出契约版本,
            决定用哪个 DTO 校验(v2 / v3 / v4-performance)
    """

    # 根不是 JSON 对象:整体不可读,fatal,不产生归一化 payload
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

    # 原始 payload 深拷贝留作证据;所有整理只作用于归一化副本,不污染证据
    raw_payload = deepcopy(payload)
    normalized: dict[str, object] = deepcopy(payload)
    shots = normalized.get("shots")
    # 缺少镜头列表或类型不对:同样整体不可读(fatal)
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
        # 镜头条目不是对象:无法安全归一化,整体 fatal
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
        # 空的零时长占位镜头:忽略并记 warning,不计入有意义镜头
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

    # 所有镜头都是空占位:没有任何可用内容,结果整体 invalid(fatal)
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

    # 剔除占位后重建镜头列表;后续问题路径按新下标报告
    normalized["shots"] = meaningful_shots
    for shot_index, shot in enumerate(meaningful_shots):
        assert isinstance(shot, dict)
        # 只有新结果才做 blocking 别名归位;legacy 重解释保持当年的解释不变
        if not legacy:
            issues.extend(_normalize_blocking_paths(shot, shot_index))
        issues.extend(_creative_density_issues(shot, shot_index))
        for field in _REQUIRED_PROFESSIONAL_FIELDS:
            # legacy 旧结果没有 v4 专业字段:逐项记 blocking 要求人工补充,
            # 而不是把整个结果判为 invalid
            if legacy and shot.get(field) is None:
                issues.append(
                    DirectorValidationIssue(
                        code="required_content_missing",
                        severity="blocking",
                        path=f"shots.{shot_index}.{field}",
                        message="该项是采用分镜前必须补充的内容。",
                        suggested_action="补充此项后，从已有结果创建待确认版本。",
                    )
                )

    # 契约外的额外 Provider 字段是"证据",不是 ShotSpec 规范字段:只移除 pydantic
    # 指认的 extra_forbidden 字段(模型原值留档为 warning)后重试;
    # 其余校验错误一律保留为阻断采用的 blocking 问题。
    while True:
        try:
            # 按冻结的契约版本选 DTO:legacy/v2 用基础计划,v3 用专业输出,
            # v4-performance 用表演输出(每镜必须含 actionBeats);未知版本直接报错
            if legacy or output_contract_revision == "professional-director-v2":
                contract = DirectorPlanPayload
            elif output_contract_revision == "professional-director-v3":
                contract = ProfessionalDirectorOutput
            elif output_contract_revision == DIRECTOR_OUTPUT_CONTRACT:
                contract = PerformanceDirectorOutput
            else:
                raise ValueError(f"unsupported director contract: {output_contract_revision}")
            plan = contract.model_validate(normalized)
            # 文字结构校验判断不了画面效果:固定追加一条提示人工复核末镜结尾的 warning
            issues.append(
                DirectorValidationIssue(
                    code="ending_review",
                    severity="warning",
                    path=f"shots.{len(plan.shots) - 1}.continuity.finalFrame",
                    message="结尾已按原文保留；文字结构校验不能判断最终画面的动作是否自然可见。",
                    suggested_action="结合末镜走位、微动作、物理变化与最终画面复核，并在成片中确认结尾效果。",
                    provider_value=plan.shots[-1].continuity.final_frame,
                )
            )
            # 只有 fatal/blocking 会阻断采用;问题全部为 warning 时结果仍是 candidate_ready
            blocked = any(issue.severity != "warning" for issue in issues)
            return DirectorNormalizationResult(
                raw_payload=raw_payload,
                normalized_payload=normalized,
                disposition="needs_input" if blocked else "candidate_ready",
                issues=tuple(issues),
                # 被阻断时不下发 plan,防止未经人工处理的结果被自动采用
                plan=None if blocked else plan,
                normalization_revision=(
                    "director-normalizer-v1" if legacy else DIRECTOR_NORMALIZATION_REVISION
                ),
            )
        except ValidationError as exc:
            # 只挑出"额外字段"错误做确定性移除;其他错误无法靠删字段解决
            extra_errors = [
                error
                for error in exc.errors(include_url=False)
                if error["type"] == "extra_forbidden"
            ]
            removed_any = False
            for error in extra_errors:
                location = tuple(error["loc"])
                # 移除前先读出模型原值:额外说明作为证据留档在生成记录中
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
            # 有字段被移除:重新走契约校验(移除后可能暴露更深层的问题)
            if removed_any:
                continue
            # 同一路径已记过 blocking(如 blocking_path_conflict)时,不再重复追加 pydantic 的通用报错
            conflicts = {issue.path for issue in issues if issue.severity == "blocking"}
            blocking = tuple(
                _blocking_issue(error)
                for error in exc.errors(include_url=False)
                if _path_text(tuple(error["loc"])) not in conflicts
            )
            return DirectorNormalizationResult(
                raw_payload=raw_payload,
                normalized_payload=normalized,
                disposition="needs_input",
                issues=tuple(issues) + blocking,
                normalization_revision=(
                    "director-normalizer-v1" if legacy else DIRECTOR_NORMALIZATION_REVISION
                ),
            )


def director_provider_output_schema() -> dict[str, object]:
    """生成下发给 Provider 的 JSON-Schema:只描述可解析的输出结构,不写入创意密度上限。

    创意列表字段(声音/微动作/风险等)的 maxItems 被递归移除 ——
    密度问题留给 _creative_density_issues 以 warning 提示,不在契约层面限制模型发挥。
    """

    # 深拷贝后再原地修改,不动模型类生成的原始 schema
    schema: dict[str, object] = deepcopy(PerformanceDirectorOutput.model_json_schema(by_alias=True))

    def visit(node: object, field_name: str | None = None) -> None:
        # 递归遍历:properties 的子节点带各自字段名,其余节点(items/anyOf/$defs 等)
        # 继承当前字段名,保证只对创意列表字段摘除 maxItems
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
