"""角色参考绑定与重制契约 —— Canon / CatReference 的 DTO 与工具函数。

文件职责:
1. 定义 ReferenceBindingDto / CharacterRemakeCommand 等 Pydantic DTO(给前端 / API 用)
2. 提供 remake_preview / remake_request_hash / remake_story_context 工具函数
3. 通过 CharacterRemakeRecord(source_id / target_id)记录"哪个 series 重制自哪个"

注意:
- 本文件只定义 DTO + 纯函数,不与数据库直接交互
- Repository 在 catflow.infrastructure.postgres_repository 中
- Worker 在 catflow_worker 中调用以实际执行重制

调用方:
- application/service.py::preview_character_remake —— 重制 preview
- application/service.py::create_character_remake —— 实际重制
- worker/catflow_worker/character_remake.py —— 执行重制任务
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from catflow.domain.contract import ContractModel

ReferenceScope = Literal["project", "series"]


class ReferenceBindingCommand(ContractModel):
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    expected_canon_profile_id: uuid.UUID = Field(alias="expectedCanonProfileId")


class ReferenceBindingDto(ContractModel):
    scope: ReferenceScope
    object_id: uuid.UUID = Field(alias="objectId")
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    label: str
    cat_identity: str = Field(alias="catIdentity")
    can_change: bool = Field(alias="canChange")
    blocked_reason: str | None = Field(alias="blockedReason", default=None)
    owner_series_id: uuid.UUID | None = Field(alias="ownerSeriesId", default=None)
    production_started_at: datetime | None = Field(alias="productionStartedAt", default=None)


class CharacterRemakePreviewCommand(ContractModel):
    source_type: ReferenceScope = Field(alias="sourceType")
    source_id: uuid.UUID = Field(alias="sourceId")
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    title: str | None = Field(default=None, min_length=1, max_length=160)


class CharacterRemakeCommand(CharacterRemakePreviewCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class CharacterRemakePreviewDto(CharacterRemakePreviewCommand):
    title: str
    input_hash: str = Field(alias="inputHash")
    target_label: str = Field(alias="targetLabel")
    source_snapshot: dict[str, Any] = Field(alias="sourceSnapshot")
    included: list[str] = Field(
        default_factory=lambda: ["故事输入与用户要求", "制作参数", "原文来源"]
    )
    excluded: list[str] = Field(
        default_factory=lambda: ["旧分镜与模型提案", "环境与成片", "任务和连续性状态"]
    )


class CharacterRemakeDto(ContractModel):
    id: uuid.UUID
    source_type: ReferenceScope = Field(alias="sourceType")
    source_id: uuid.UUID = Field(alias="sourceId")
    target_id: uuid.UUID = Field(alias="targetId")
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    created_at: datetime = Field(alias="createdAt")


def remake_preview(
    command: CharacterRemakePreviewCommand, snapshot: dict[str, Any], *, canon_hash: str, label: str
) -> CharacterRemakePreviewDto:
    """生成重制 preview DTO。

    流程:
    1. 构造 frozen_input 文档(含 source / canon / title / sourceSnapshot)
    2. SHA256 算 input_hash(防止 caller 改 source 后悄悄重制)
    3. 返回 DTO 供前端展示

    Args:
        command: 重制 preview 命令(source_type / source_id / canon_profile_id / 可选 title)
        snapshot: 源 series 的快照(输入 / 资产 / 连续性 / jobs)
        canon_hash: Canon profile 的 SHA256(冻结到 input 文档)
        label: 重制标签(如 "V4 白猫重制"),用在默认 title

    Returns:
        CharacterRemakePreviewDto,含 input_hash / sourceSnapshot / included / excluded
    """
    title = command.title or f"{snapshot['input']['title']} · {label}重制"[:160]
    document = {
        "sourceType": command.source_type,
        "sourceId": str(command.source_id),
        "canonProfileId": str(command.canon_profile_id),
        "canonProfileHash": canon_hash,
        "title": title,
        "sourceSnapshot": snapshot,
    }
    digest = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CharacterRemakePreviewDto(
        **command.model_dump(include={"source_type", "source_id", "canon_profile_id"}),
        title=title,
        inputHash=digest,
        targetLabel=label,
        sourceSnapshot=snapshot,
    )


def remake_request_hash(command: CharacterRemakeCommand) -> str:
    """计算 CharacterRemakeCommand 的 SHA256,用作 frozen input 校验。

    排除 idempotency_key(防重放 —— 同一指令两次的 idempotency_key 可能相同)。
    """
    document = command.model_dump(mode="json", by_alias=True, exclude={"idempotency_key"})
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def remake_story_context(snapshot: dict[str, Any] | None) -> str:
    """从源 snapshot 拼接"重制继承的原始输入"段,嵌入重制 prompt。

    CatFlow 的关键约束:原文保持原样,本次猫咪外观以所选 Canon 为准,保留故事动作因果。
    """
    if not snapshot:
        return ""
    texts = [item["rawText"] for item in snapshot.get("sources", [])]
    texts.extend(snapshot.get("userMessages", []))
    if not texts:
        return ""
    return (
        "\n【重制继承的原始输入】\n"
        + "\n".join(texts)
        + "\n原文保持原样；本次猫咪外观以所选固定参考为准，保留故事动作因果。"
    )
