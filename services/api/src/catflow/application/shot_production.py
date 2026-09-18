"""分镜(shot)媒体生产的命令与 DTO —— 从起始帧确认到成片组装的输入契约。

覆盖四类操作:
- shot_frame / shot_video 的预览(免费:只编译 prompt 与冻结输入)与付费提交
- 起始帧人工确认:5 项 checks 逐项核对后,把图片资产固定为该镜头的严格首帧
- 从真实镜头视频提取单帧(本地 ffmpeg,不付费),作为起始帧候选
- 镜头组装 ShotAssembly:按分镜顺序为每镜选一份 24fps 素材,生成剪辑草稿

与其他模块的关系:
- 本文件的命令由 service.py 的分镜任务方法消费(preview_shot_media /
  create_shot_media_job / confirm_shot_frame / extract_shot_frame /
  assemble_shot_draft),预览时调用 video_generation.py::compile_shot_media_prompt
  编译单镜 prompt(起始帧只描述动作前状态;视频严格从已确认首帧开始表演)
- shot_design_hash 是防漂移锚点:镜头设计、参考图或环境意图任一变化都会使
  已确认起始帧过期,必须重新确认后才能提交镜头视频或组装成片
"""

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
    """分镜操作定位目标 —— 当前激活分镜计划版本中的一个镜头。"""

    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    shot_id: str = Field(alias="shotId", min_length=1, max_length=80)


class ShotMediaPreviewCommand(ShotTarget):
    """镜头媒体预览命令(免费) —— 只编译 prompt 并冻结输入,不创建任务。"""

    # 生成目的:shot_frame=镜头起始帧(图片),shot_video=镜头视频(严格从已确认首帧开始)
    purpose: Literal["shot_frame", "shot_video"]


class ShotMediaCommand(ShotMediaPreviewCommand, PaidJobCommand):
    """镜头媒体付费提交命令 —— 在预览命令之上追加防漂移与防重复字段。"""

    # 防漂移:必须等于提交时重新预览算出的 inputHash(冻结输入的 SHA256);
    # 不一致说明分镜设计/参考图已变化,拒绝按过期清单付费
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    # 幂等键:同键且同输入哈希的未终结任务直接复用,防止重复付费提交
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotFrameConfirmCommand(ShotTarget):
    """起始帧人工确认命令 —— 创作者核对画面后,把图片资产固定为该镜头的严格首帧。"""

    # 被确认的起始画面:必须是本项目图片,或该镜头已绑定的真实参考图
    asset_id: uuid.UUID = Field(alias="assetId")
    # 防漂移:确认时看到的分镜设计哈希(shot_design_hash);
    # 镜头设计/参考图/环境意图一旦变化哈希即不同,已确认起始帧随之过期
    expected_design_hash: str = Field(alias="expectedDesignHash", pattern=r"^[a-f0-9]{64}$")
    # 人工逐项核对,5 项必须齐全且不重复:
    # identity_scale=身份比例 / placement_state=落位状态 / movement_space=运动空间
    # / action_start=动作起点 / continuity=连续性
    checks: list[
        Literal["identity_scale", "placement_state", "movement_space", "action_start", "continuity"]
    ] = Field(min_length=5, max_length=5)


class ShotFrameExtractCommand(ShotTarget):
    """视频取帧命令 —— 用本地 ffmpeg 从真实镜头视频提取一帧,作为起始帧候选(不付费)。"""

    # 源视频资产:必须是本项目视频,且待取帧号在其有效范围内
    source_video_asset_id: uuid.UUID = Field(alias="sourceVideoAssetId")
    frame: int = Field(ge=0)  # 要提取的帧号(0 起),需小于源视频总帧数
    # 幂等键:同键同输入直接复用已有任务,防止重复创建取帧任务
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotTake(ContractModel):
    """组装素材条目 —— 一个镜头选用的一份镜头视频资产及其入点帧。"""

    shot_id: str = Field(alias="shotId", min_length=1)
    # 素材资产:必须是本项目、role=shot_video 且属于当前镜头设计(设计哈希一致)的视频
    asset_id: uuid.UUID = Field(alias="assetId")
    # 素材入点帧(0 起):从此帧截取 镜头时长×24 帧;要求素材为 24fps 且剩余长度足够,
    # 不做自动变速或补帧
    source_in_frame: int = Field(alias="sourceInFrame", ge=0)


class ShotAssemblyCommand(ContractModel):
    """镜头组装命令 —— 按当前分镜顺序为每个镜头选一份素材,生成 9:16 剪辑草稿(EDL v3)。"""

    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    # 与当前分镜版本的镜头列表等长且顺序一致(分镜最多 4 镜)
    takes: list[ShotTake] = Field(min_length=1, max_length=4)
    # 幂等键:同键同输入(草稿输入哈希不含此键)直接复用已有草稿,防止重复创建
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


def shot_design_hash(shot: ShotSpec, references: list[dict], environment_intent: str) -> str:
    """计算镜头设计哈希(SHA256) —— 起始帧确认、镜头视频提交与组装共用的防漂移锚点。

    哈希输入 = 镜头设计(不含 confirmed_frame)+ 参考图清单(assetId/sha256/role)+ 环境意图;
    任何一项变化都会产生新哈希,使已确认的起始帧过期(frameCurrent=False),
    必须重新人工确认后才能继续生产。
    """

    # 有意排除确认结果本身:挑选起始帧这一动作不能使自己过期。
    shot_document = shot.model_dump(mode="json", by_alias=True, exclude={"confirmed_frame"})
    # 新增设计字段为空时不携带设计信息;从哈希输入中删去,保住历史确认仍然有效。
    for field in ("cameraSpatialRelation", "interactionConstraints", "visualExclusions"):
        if not shot_document[field]:
            del shot_document[field]
    document = {
        "shot": shot_document,
        "references": references,
        "environmentIntent": environment_intent,
    }
    # 规范化 JSON(键排序、紧凑分隔符、不转义中文)后取 SHA256,保证哈希稳定可复现
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
