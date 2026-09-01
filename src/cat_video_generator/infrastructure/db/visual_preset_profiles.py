"""Persistence helpers for versioned Canon visual presets.

This module owns the shared database boundary used by recipe instantiation and
the public material-library API.  It deliberately reuses existing Asset and
VisualProfileRevision rows instead of creating another asset model.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_BOARD_KEY,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_POSITIVE,
    CANON_V4_STYLE_SOURCE_EXCLUSIONS,
    CANON_V4_STYLE_SOURCE_KEY,
    ReferenceAuthorityRole,
    VisualPresetKey,
    canon_reference_keys,
)
from .models import (
    Asset,
    ProductionRecipeInstance,
    ProductionRun,
    Subject,
    SubjectReference,
    SubjectRevision,
    VisualProfileRevision,
)
from .repositories import RecordNotFoundError, WorkflowConflictError

CANON_V3_REQUIRED_KEYS = canon_reference_keys(CANON_V3_PROFILE_ID, "indoor")
CANON_V4_REQUIRED_KEYS = canon_reference_keys(CANON_V4_PROFILE_ID, "indoor")

_REFERENCE_TITLES = {
    "person:headshot": "儿童面部",
    "person:fullbody": "儿童全身比例",
    "cat:front": "猫咪正面",
    "cat:side": "猫咪侧面",
    "style:line_texture": "线条材质",
    CANON_V4_STYLE_SOURCE_KEY: "叶片材质与柔光来源（仅提炼）",
    CANON_V4_STYLE_BOARD_KEY: "原创治愈线条材质画风板 v4",
}


def canon_subject_documents(canon_profile_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the versioned child and cat identity contracts for one Canon release."""

    if canon_profile_id not in {CANON_V3_PROFILE_ID, CANON_V4_PROFILE_ID}:
        raise ValueError(f"不支持的 Canon 配置：{canon_profile_id}")
    is_v4 = canon_profile_id == CANON_V4_PROFILE_ID

    return (
        {
            "name": "固定儿童",
            "kind": "person",
            "role": "protagonist",
            "identityAnchors": [
                (
                    "脸型、五官、8–9 岁年龄感、深棕黑色齐下颌短发、刘海与发际线"
                    "由 Canon-v4 人物参考锁定"
                    if is_v4
                    else "脸部、年龄、五官与短发由 Canon-v3 人物参考锁定"
                ),
                (
                    "全片保持同一 8–9 岁儿童身体比例，不成人化"
                    if is_v4
                    else "全片保持同一儿童身体比例，不成人化"
                ),
            ],
            "immutableTraits": (
                [
                    "固定柔和圆润脸型与五官比例",
                    "固定深棕黑色齐下颌短发、刘海、发际线与发色",
                    "固定 8–9 岁儿童身体比例",
                ]
                if is_v4
                else ["固定五官", "固定短发", "儿童身体比例"]
            ),
            "relationshipNotes": "与固定猫咪相互信任，以轻微动作和目光完成日常互动。",
            "dramaticFunction": "主动完成一个低压力的日常小行动。",
            "visualRisks": [
                "年龄漂移",
                "脸部漂移",
                "马尾、长发或基础发型轮廓变化",
                "身体比例成人化",
                "本集服装跨连续镜头变化",
            ],
            "references": [
                {
                    "semanticKey": "person:headshot",
                    "semanticRole": "front",
                    "instruction": "锁定脸型、五官、8–9 岁年龄感、齐下颌短发、刘海和发际线。",
                },
                {
                    "semanticKey": "person:fullbody",
                    "semanticRole": "full_body",
                    "instruction": "锁定儿童身体比例与整体轮廓。",
                },
            ],
        },
        {
            "name": "固定猫咪",
            "kind": "animal",
            "role": "co_protagonist",
            "identityAnchors": [
                (
                    "头脸、金棕色眼睛、粉色鼻口、灰白毛色分区、主要虎斑、体型与环纹尾巴"
                    "由 Canon-v4 猫咪参考锁定"
                    if is_v4
                    else "脸部、毛色分区、体型与环纹尾巴由 Canon-v3 猫咪参考锁定"
                ),
                "保持猫科身体结构与四足姿态",
            ],
            "immutableTraits": [
                "固定圆润头脸、眼睛与粉色鼻口",
                "固定灰白毛色分区与主要虎斑位置",
                "固定紧凑体型、四足猫科结构与尾巴环纹",
            ],
            "relationshipNotes": "用耳朵、尾巴、步态和靠近动作回应儿童。",
            "dramaticFunction": "参与小变化并促成温暖收尾。",
            "visualRisks": ["毛色漂移", "尾巴纹路漂移", "人形肢体", "多余肢体"],
            "references": [
                {
                    "semanticKey": "cat:front",
                    "semanticRole": "front",
                    "instruction": "锁定猫咪脸部、眼睛与正面毛色分区。",
                },
                {
                    "semanticKey": "cat:side",
                    "semanticRole": "side",
                    "instruction": "锁定猫咪体型、侧面虎斑与环纹尾巴。",
                },
            ],
        },
    )


def canon_v3_subject_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """Compatibility contract for historical v3 recipe and migration tests."""

    return canon_subject_documents(CANON_V3_PROFILE_ID)


def ensure_canon_subjects(
    session: Session,
    *,
    project_id: uuid.UUID,
    assets_by_key: dict[str, Asset],
    canon_profile_id: str,
) -> dict[str, Subject]:
    """Reuse an exact approved evidence binding or create a new subject revision."""

    existing_subjects = list(
        session.scalars(
            select(Subject)
            .where(Subject.production_run_id == project_id)
            .order_by(Subject.created_at, Subject.id)
        )
    )
    resolved: dict[str, Subject] = {}
    for document in canon_subject_documents(canon_profile_id):
        expected_asset_ids = {
            assets_by_key[reference["semanticKey"]].id for reference in document["references"]
        }
        expected_revision_hash = _canon_subject_revision_hash(document, assets_by_key)
        subject = next(
            (
                candidate
                for candidate in existing_subjects
                if candidate.kind == document["kind"]
                and candidate.role == document["role"]
                and candidate.current_revision_id is not None
                and _approved_subject_asset_ids(
                    session, candidate.current_revision_id
                )
                == expected_asset_ids
                and (
                    current_revision := session.get(
                        SubjectRevision, candidate.current_revision_id
                    )
                )
                is not None
                and current_revision.revision_hash == expected_revision_hash
            ),
            None,
        )
        if subject is None:
            subject = next(
                (
                    candidate
                    for candidate in existing_subjects
                    if candidate.kind == document["kind"]
                    and candidate.role == document["role"]
                ),
                None,
            )
            if subject is None:
                subject = Subject(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    kind=str(document["kind"]),
                    role=str(document["role"]),
                    status="ready",
                )
                session.add(subject)
                session.flush()
                existing_subjects.append(subject)
            _add_canon_subject_revision(
                session,
                subject=subject,
                document=document,
                assets_by_key=assets_by_key,
            )
        resolved[str(document["role"])] = subject
    return resolved


def _approved_subject_asset_ids(
    session: Session,
    revision_id: uuid.UUID,
) -> set[uuid.UUID]:
    revision = session.get(SubjectRevision, revision_id)
    if revision is None or revision.approval_status != "approved":
        return set()
    return set(
        session.scalars(
            select(SubjectReference.asset_id).where(
                SubjectReference.subject_revision_id == revision_id
            )
        )
    )


def _add_canon_subject_revision(
    session: Session,
    *,
    subject: Subject,
    document: dict[str, Any],
    assets_by_key: dict[str, Asset],
) -> None:
    current_revision = (
        None
        if subject.current_revision_id is None
        else session.get(SubjectRevision, subject.current_revision_id)
    )
    revision = SubjectRevision(
        id=uuid.uuid4(),
        subject_id=subject.id,
        revision=1 if current_revision is None else current_revision.revision + 1,
        name=str(document["name"]),
        identity_anchors_json=list(document["identityAnchors"]),
        immutable_traits_json=list(document["immutableTraits"]),
        relationship_notes=str(document["relationshipNotes"]),
        dramatic_function=str(document["dramaticFunction"]),
        visual_risks_json=list(document["visualRisks"]),
        revision_hash=_canon_subject_revision_hash(document, assets_by_key),
        approval_status="approved",
    )
    session.add(revision)
    session.flush()
    subject.status = "ready"
    subject.current_revision_id = revision.id
    for order, reference in enumerate(document["references"], 1):
        session.add(
            SubjectReference(
                id=uuid.uuid4(),
                subject_revision_id=revision.id,
                asset_id=assets_by_key[reference["semanticKey"]].id,
                semantic_role=str(reference["semanticRole"]),
                sort_order=order,
                instruction=str(reference["instruction"]),
            )
        )


def _canon_subject_revision_document(
    document: dict[str, Any],
    assets_by_key: dict[str, Asset],
) -> dict[str, Any]:
    references = [
        {
            "assetId": str(assets_by_key[reference["semanticKey"]].id),
            "semanticRole": reference["semanticRole"],
            "instruction": reference["instruction"],
        }
        for reference in document["references"]
    ]
    return {
        **{key: value for key, value in document.items() if key != "references"},
        "references": references,
    }


def _canon_subject_revision_hash(
    document: dict[str, Any],
    assets_by_key: dict[str, Asset],
) -> str:
    return hashlib.sha256(
        json.dumps(
            _canon_subject_revision_document(document, assets_by_key),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def load_canon_assets(
    session: Session,
    keys: tuple[str, ...],
) -> dict[str, Asset]:
    assets = list(
        session.scalars(
            select(Asset).where(
                Asset.scope == "canon",
                Asset.status.in_(("ready", "approved")),
                Asset.semantic_key.in_(keys),
            )
        )
    )
    by_key = {asset.semantic_key: asset for asset in assets if asset.semantic_key}
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise WorkflowConflictError("缺少 Canon 参考：" + ", ".join(missing))
    return by_key


def visual_reference_json(asset: Asset, *, required: bool = True) -> dict[str, Any]:
    semantic_key = asset.semantic_key or ""
    content_url = f"/api/v1/assets/{asset.id}/content"
    authority = _reference_authority_json(semantic_key)
    return {
        "assetId": str(asset.id),
        "semanticKey": semantic_key,
        "title": _REFERENCE_TITLES.get(semantic_key, semantic_key or "视觉参考"),
        "contentUrl": content_url,
        "thumbnailUrl": content_url,
        "approvalStatus": asset.status,
        "sha256": asset.sha256,
        "required": required,
        "authority": authority,
    }


def visual_profile_bindings(
    assets_by_key: dict[str, Asset],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "assetId": str(assets_by_key[key].id),
            "purpose": _reference_purpose(key),
            "instruction": _profile_binding_instruction(key),
            "authority": _reference_authority_json(key),
        }
        for key in keys
    ]


def generation_reference_bindings(
    assets_by_key: dict[str, Asset],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "assetId": str(assets_by_key[key].id),
            "usage": "generation_reference",
            "role": "style" if key.startswith("style:") else "identity",
            "applyTo": "anchor",
            "authority": _reference_authority_json(key),
        }
        for key in keys
    ]


def ensure_canon_visual_profile(
    session: Session,
    *,
    project_id: uuid.UUID,
    canon_profile_id: str,
    assets_by_key: dict[str, Asset] | None = None,
) -> VisualProfileRevision:
    required_keys = canon_reference_keys(canon_profile_id, "indoor")
    resolved_assets = assets_by_key or load_canon_assets(session, required_keys)
    snapshot = [
        {
            **visual_reference_json(resolved_assets[key]),
            "role": "style" if key.startswith("style:") else "identity",
        }
        for key in required_keys
    ]
    bindings = visual_profile_bindings(resolved_assets, required_keys)
    style_positive = (
        CANON_V4_STYLE_POSITIVE
        if canon_profile_id == CANON_V4_PROFILE_ID
        else CANON_V3_STYLE_POSITIVE
    )
    style_negative = (
        CANON_V4_STYLE_NEGATIVE
        if canon_profile_id == CANON_V4_PROFILE_ID
        else CANON_V3_STYLE_NEGATIVE
    )
    profile_document = {
        "canonProfileId": canon_profile_id,
        "bindings": bindings,
        "references": snapshot,
        "stylePositive": list(style_positive),
        "styleExcluded": list(style_negative),
        "sourceExclusions": (
            list(CANON_V4_STYLE_SOURCE_EXCLUSIONS)
            if canon_profile_id == CANON_V4_PROFILE_ID
            else []
        ),
    }
    profile_hash = hashlib.sha256(
        json.dumps(profile_document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    profile = session.scalar(
        select(VisualProfileRevision).where(
            VisualProfileRevision.production_run_id == project_id,
            VisualProfileRevision.profile_hash == profile_hash,
        )
    )
    if profile is None:
        revision = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(VisualProfileRevision.revision), 0)).where(
                        VisualProfileRevision.production_run_id == project_id
                    )
                )
                or 0
            )
            + 1
        )
        profile = VisualProfileRevision(
            id=uuid.uuid4(),
            production_run_id=project_id,
            revision=revision,
            profile_hash=profile_hash,
            source_profile_id=canon_profile_id,
            person_identity=(
                "固定同一名 8–9 岁儿童的柔和圆润脸型、眼形眼距、眉形、鼻口比例与耳朵位置"
                if canon_profile_id == CANON_V4_PROFILE_ID
                else "固定儿童脸型、五官、年龄感与身份特征"
            ),
            person_hair=(
                "固定深棕黑色齐下颌短发、刘海方向、发际线与基础发型轮廓；马尾和长发不属于服装变化"
                if canon_profile_id == CANON_V4_PROFILE_ID
                else "固定儿童短发轮廓、发色与发际线"
            ),
            person_body=(
                "固定 8–9 岁儿童头身比例、肩宽、四肢长度与手脚尺度，不成人化"
                if canon_profile_id == CANON_V4_PROFILE_ID
                else "固定儿童全身比例与非成人化身体结构"
            ),
            cat_identity=(
                "固定同一只灰白虎斑猫的圆润头脸、金棕色眼睛、粉色鼻口、灰白分区、"
                "主要虎斑、紧凑体型、四足结构与尾巴环纹"
                if canon_profile_id == CANON_V4_PROFILE_ID
                else "固定猫咪脸部、毛色分区、体型与环纹尾巴"
            ),
            style_positive_json=list(style_positive),
            style_negative_json=list(style_negative),
            reference_bindings_json=bindings,
            reference_snapshot_json=snapshot,
        )
        session.add(profile)
        session.flush()
    project = session.get(ProductionRun, project_id)
    if project is None:
        raise RecordNotFoundError(f"ProductionRun not found: {project_id}")
    project.current_visual_profile_revision_id = profile.id
    project.default_reference_bindings_json = generation_reference_bindings(
        resolved_assets, required_keys
    )
    return profile


def visual_preset_profile_json(
    session: Session,
    preset_key: VisualPresetKey,
) -> dict[str, Any]:
    is_v4 = preset_key is VisualPresetKey.HEALING_CHILD_CAT_STYLE_BOARD_V4
    canon_profile_id = CANON_V4_PROFILE_ID if is_v4 else CANON_V3_PROFILE_ID
    required_keys = CANON_V4_REQUIRED_KEYS if is_v4 else CANON_V3_REQUIRED_KEYS
    display_keys = (
        (*required_keys[:-1], CANON_V4_STYLE_SOURCE_KEY, required_keys[-1])
        if is_v4
        else required_keys
    )
    assets = list(
        session.scalars(
            select(Asset).where(
                Asset.scope == "canon",
                Asset.status.in_(("ready", "approved")),
                Asset.semantic_key.in_(display_keys),
            )
        )
    )
    assets_by_key = {asset.semantic_key: asset for asset in assets if asset.semantic_key}
    slots = []
    for key in display_keys:
        role = (
            "style" if key.startswith("style:") else ("cat" if key.startswith("cat:") else "person")
        )
        if key == CANON_V4_STYLE_SOURCE_KEY:
            role = "style"
        asset = assets_by_key.get(key)
        reference = (
            {
                "assetId": None,
                "semanticKey": key,
                "title": _REFERENCE_TITLES[key],
                "contentUrl": None,
                "thumbnailUrl": None,
                "approvalStatus": "missing",
                "sha256": None,
                "required": key in required_keys,
                "authority": _reference_authority_json(key),
            }
            if asset is None
            else visual_reference_json(asset, required=key in required_keys)
        )
        slots.append(
            {
                **reference,
                "role": role,
                "purpose": "style" if role == "style" else "identity",
                "instruction": _profile_binding_instruction(key),
            }
        )
    return {
        "key": preset_key.value,
        "canonProfileId": canon_profile_id,
        "title": "一人一猫 · 原创治愈画风 v4" if is_v4 else "一人一猫 · 线条材质（历史 v3）",
        "description": (
            "固定 8–9 岁儿童、固定灰白虎斑猫；叶片照片仅作提炼血缘，Provider 只接收纯画风板"
            if is_v4
            else "历史预设：固定儿童、固定猫咪，并直接使用旧线条材质参考"
        ),
        "version": 4 if is_v4 else 3,
        "ready": all(key in assets_by_key for key in required_keys),
        "slots": slots,
    }


def episode_visual_profile_json(
    session: Session,
    profile: VisualProfileRevision,
) -> dict[str, Any]:
    recipe_instance_id = session.scalar(
        select(ProductionRecipeInstance.id)
        .where(
            ProductionRecipeInstance.production_run_id == profile.production_run_id,
            ProductionRecipeInstance.lifecycle_status == "active",
        )
        .order_by(ProductionRecipeInstance.revision.desc())
        .limit(1)
    )
    snapshot = [dict(reference) for reference in profile.reference_snapshot_json]
    asset_ids = {
        uuid.UUID(str(reference["assetId"]))
        for reference in snapshot
        if reference.get("assetId")
    }
    subject_authorities: dict[
        uuid.UUID,
        tuple[Subject, SubjectRevision, str, bool],
    ] = {}
    if asset_ids:
        rows = session.execute(
            select(SubjectReference, SubjectRevision, Subject, Asset)
            .join(
                SubjectRevision,
                SubjectRevision.id == SubjectReference.subject_revision_id,
            )
            .join(Subject, Subject.id == SubjectRevision.subject_id)
            .join(Asset, Asset.id == SubjectReference.asset_id)
            .where(
                SubjectReference.asset_id.in_(asset_ids),
                Subject.production_run_id == profile.production_run_id,
            )
            .order_by(
                Subject.role,
                Subject.id,
                SubjectRevision.revision.desc(),
                SubjectReference.sort_order,
            )
        )
        for reference, revision, subject, asset in rows:
            is_current_approved = bool(
                subject.current_revision_id == revision.id
                and revision.approval_status == "approved"
            )
            existing = subject_authorities.get(reference.asset_id)
            if existing is None or (is_current_approved and not existing[3]):
                subject_authorities[reference.asset_id] = (
                    subject,
                    revision,
                    asset.status,
                    is_current_approved,
                )
    enriched_references: list[dict[str, Any]] = []
    for reference in snapshot:
        asset_id = uuid.UUID(str(reference["assetId"])) if reference.get("assetId") else None
        subject_authority = None if asset_id is None else subject_authorities.get(asset_id)
        authority = reference.get("authority")
        authority_role = (
            str(authority.get("role") or "")
            if isinstance(authority, dict)
            else ""
        )
        subject = None if subject_authority is None else subject_authority[0]
        revision = None if subject_authority is None else subject_authority[1]
        asset_status = None if subject_authority is None else subject_authority[2]
        current_subject_revision = False if subject_authority is None else subject_authority[3]
        enriched_references.append(
            {
                **reference,
                "visualProfileRevisionId": str(profile.id),
                "authorityOrigin": (
                    "subject_revision" if subject_authority is not None else "visual_profile"
                ),
                "currentAuthority": bool(
                    reference.get("approvalStatus") == "approved"
                    and (
                        (
                            subject_authority is not None
                            and current_subject_revision
                            and asset_status == "approved"
                        )
                        or (subject_authority is None and authority_role != "identity")
                    )
                ),
                "subjectId": None if subject is None else str(subject.id),
                "subjectRevisionId": None if revision is None else str(revision.id),
                "subjectRevision": None if revision is None else revision.revision,
                "subjectKind": None if subject is None else subject.kind,
                "subjectRole": None if subject is None else subject.role,
            }
        )
    return {
        "id": str(profile.id),
        "projectId": str(profile.production_run_id),
        "recipeInstanceId": (
            None if recipe_instance_id is None else str(recipe_instance_id)
        ),
        "revision": profile.revision,
        "sourceProfileId": profile.source_profile_id,
        "personIdentity": profile.person_identity,
        "personHair": profile.person_hair,
        "personBody": profile.person_body,
        "catIdentity": profile.cat_identity,
        "stylePositive": profile.style_positive_json,
        "styleNegative": profile.style_negative_json,
        "referenceBindings": profile.reference_bindings_json,
        "references": enriched_references,
        "lockedSemanticKeys": list(canon_reference_keys(profile.source_profile_id, "indoor")),
        "sourceExclusions": (
            list(CANON_V4_STYLE_SOURCE_EXCLUSIONS)
            if profile.source_profile_id == CANON_V4_PROFILE_ID
            else []
        ),
        "createdAt": profile.created_at.isoformat(),
    }


def _reference_purpose(key: str) -> str:
    if key == "person:headshot":
        return "person_identity"
    if key == "person:fullbody":
        return "person_body"
    if key.startswith("cat:"):
        return "cat_identity"
    if key.startswith("style:"):
        return "style"
    raise ValueError(f"不支持的视觉档案语义键：{key}")


def _profile_binding_instruction(key: str) -> str:
    if key == CANON_V4_STYLE_SOURCE_KEY:
        return "仅保留材质、柔和漫射光、清透高光和空间层次血缘；不会提交给日常 Provider 请求。"
    if key == CANON_V4_STYLE_BOARD_KEY:
        return "唯一可提交 Provider 的画风来源；只控制轮廓线、材质、色阶与光影，不添加具体内容。"
    if key == "style:line_texture":
        return "历史 v3 画风参考；只提取材质与光线，不复制叶片、露珠、绿色配色或微距构图。"
    return "固定身份视觉证据，不允许普通参考替换。"


def _reference_authority_json(key: str) -> dict[str, Any]:
    if key == CANON_V4_STYLE_SOURCE_KEY:
        return {
            "role": ReferenceAuthorityRole.STYLE_SOURCE.value,
            "providerEligible": False,
            "priority": 10,
            "lockedTraits": [],
            "mutableTraits": [],
            "forbiddenTransfer": list(CANON_V4_STYLE_SOURCE_EXCLUSIONS),
        }
    if key.startswith("style:"):
        return {
            "role": ReferenceAuthorityRole.STYLE_BOARD.value,
            "providerEligible": True,
            "priority": 50,
            "lockedTraits": ["轮廓线", "材质", "色阶", "光影"],
            "mutableTraits": ["与剧情一致的场景颜色和物体"],
            "forbiddenTransfer": ["具体人物或动物身份", "具体物体与构图"],
        }
    if key.startswith("person:"):
        return {
            "role": ReferenceAuthorityRole.IDENTITY.value,
            "providerEligible": True,
            "priority": 100,
            "lockedTraits": ["脸型", "五官", "8–9 岁年龄感", "齐下颌短发", "发色", "儿童身体比例"],
            "mutableTraits": ["服装", "鞋袜", "轻量配件", "表情", "动作", "朝向"],
            "forbiddenTransfer": ["背景", "旧服装"],
        }
    return {
        "role": ReferenceAuthorityRole.IDENTITY.value,
        "providerEligible": True,
        "priority": 100,
        "lockedTraits": ["头脸", "眼睛", "鼻口", "灰白分区", "主要虎斑", "四足结构", "尾巴环纹"],
        "mutableTraits": ["姿态", "表情", "朝向", "已批准轻量配件"],
        "forbiddenTransfer": ["背景", "人形肢体", "未批准配件"],
    }
