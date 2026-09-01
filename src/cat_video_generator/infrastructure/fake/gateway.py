"""Deterministic provider boundary for local UI acceptance.

This module is a test double only.  The production composition root never
imports or selects it, and Web runtime settings cannot expose it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from ...application.ports import (
    CreativeDirectorResult,
    DirectorResult,
    ImageDiagnosticResult,
    ImageResult,
    VideoDiagnosticResult,
    VideoTaskResult,
)
from ...domain.rendering import VideoInputPlan


def _request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class FakeArkGateway:
    """Small deterministic Ark substitute that is never wired by production startup."""

    model = "fake-doubao-planner"
    analysis_model = model
    image_model = "fake-seedream"
    video_model = "fake-seedance"
    review_model = "fake-video-review"

    def generate_creative_text(
        self,
        *,
        prompt: str,
        output_name: str,
    ) -> CreativeDirectorResult:
        if output_name != "StoryCandidateBatch":
            raise ValueError(
                f"fake provider has no creative fixture for {output_name}"
            )
        payload = {
            "candidates": [
                {
                    "title": "雨前收画",
                    "body": "孩子和猫发现雨云，一起把晾晒的画纸安全收回屋内。",
                }
            ]
        }
        input_text = f"生成一个{output_name}。"
        request_hash = _request_hash(
            {
                "input": input_text,
                "instructions": prompt,
                "mode": "creative_text",
                "model": self.model,
                "outputName": output_name,
            }
        )
        return CreativeDirectorResult(
            payload=payload,
            response_id=f"fake-creative-{request_hash[:10]}",
            model=self.model,
            request_hash=request_hash,
        )

    def generate_storyboard_text(
        self,
        *,
        prompt: str,
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> CreativeDirectorResult:
        if output_name != "CanvasStoryboardPlanOutput":
            raise ValueError(
                f"fake provider has no storyboard fixture for {output_name}"
            )
        payload = {
            "shots": [
                {
                    "order": index,
                    "title": f"生活镜头 {index}",
                    "direction": "人物与猫咪围绕同一生活事件完成一个连续、可见的动作。",
                    "durationSeconds": 12,
                }
                for index in range(1, 6)
            ]
        }
        request_hash = _request_hash(
            {
                "images": [str(path) for path in image_paths],
                "mode": "storyboard_text",
                "model": self.model,
                "outputName": output_name,
                "prompt": prompt,
            }
        )
        return CreativeDirectorResult(
            payload=payload,
            response_id=f"fake-storyboard-{request_hash[:10]}",
            model=self.model,
            request_hash=request_hash,
        )

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> DirectorResult:
        del schema
        payload = self._structured_payload(output_name, prompt)
        return DirectorResult(
            payload=payload,
            response_id=f"fake-{output_name.lower()}-{uuid.uuid4().hex[:10]}",
            model=self.model,
            request_hash=_request_hash(
                {
                    "outputName": output_name,
                    "prompt": prompt,
                    "payload": payload,
                    "images": [str(path) for path in image_paths],
                }
            ),
        )

    def analyze_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...],
    ) -> DirectorResult:
        del schema
        if output_name != "ShotAssistAnalysis":
            raise ValueError(f"fake provider has no multimodal fixture for {output_name}")
        payload = {
            "actionDensityAssessment": (
                "当前动作链适合一个 10 至 12 秒片段，建议保留两个连续子镜头。"
            ),
            "assetCompatibilityAssessment": (
                "参考图适合锁定角色、画风和起始状态；已批准开场图应单独作为首帧输入。"
            ),
            "pacingPlan": {
                "recommendedDurationSeconds": 10,
                "rationale": "先建立人猫位置，再完整呈现一次互动并稳定收尾。",
                "beats": [
                    {
                        "ordinal": 1,
                        "description": "建立人物、猫咪和目标物的位置",
                        "rhythm": "brief",
                    },
                    {
                        "ordinal": 2,
                        "description": "完成主要动作并停留在可见结果",
                        "rhythm": "expanded",
                    },
                ],
            },
            "recommendedSceneLookUsage": "appearance_only",
            "recommendedAnchorMode": "text_only",
            "referenceDecisions": [],
            "continuity": {
                "previousIssues": [],
                "nextIssues": [],
                "recommendation": "收尾保持人物、猫咪和关键道具位置稳定，供下一片段衔接。",
            },
            "promptRisks": [],
            "creativeBody": (
                "1. 中景固定机位，猫咪先观察目标，人物在后方准备并保持空间关系清楚。\n"
                "2. 近景轻微跟随，人物完成必要的手部操作，猫咪给出自然反馈，画面稳定收尾。"
            ),
            "creativeAlternatives": [
                {
                    "label": "stable",
                    "body": "1. 固定中景建立位置。\n2. 固定近景完成互动并停稳。",
                    "rationale": "减少运镜和状态跳变。",
                }
            ],
            "anchorBrief": "动作开始前，人物和猫咪处于稳定准备状态，关键道具完整可见。",
            "patch": None,
        }
        return DirectorResult(
            payload=payload,
            response_id=f"fake-shot-review-{uuid.uuid4().hex[:10]}",
            model=self.analysis_model,
            request_hash=_request_hash(
                {
                    "outputName": output_name,
                    "prompt": prompt,
                    "images": [str(path) for path in image_paths],
                }
            ),
        )

    def generate_image(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ImageResult:
        token = _request_hash(
            {"prompt": prompt, "references": [str(path) for path in reference_paths]}
        )[:20]
        return ImageResult(f"cvg-fake://image/{token}.png", self.image_model)

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult:
        token = _request_hash(
            {
                "prompt": prompt,
                "inputPlan": input_plan.model_dump(mode="json"),
                "sources": [str(path) for path in input_sources],
            }
        )[:20]
        task_id = (
            f"fake-video-{input_plan.duration_seconds}-{input_plan.resolution}-{token}"
        )
        return VideoTaskResult(
            task_id=task_id,
            status="succeeded",
            video_url=(
                f"cvg-fake://video/{input_plan.duration_seconds}/"
                f"{input_plan.resolution}/{token}.mp4"
            ),
            model=self.video_model,
            duration_seconds=input_plan.duration_seconds,
            ratio="9:16",
            resolution=input_plan.resolution,
            generate_audio=True,
        )

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        match = re.fullmatch(r"fake-video-(\d+)-(480p|720p)-([0-9a-f]{20})", task_id)
        if match is None:
            return VideoTaskResult(
                task_id=task_id,
                status="failed",
                error_code="fake_task_not_found",
                error_message="fake provider task id is invalid",
            )
        duration, resolution, token = match.groups()
        return VideoTaskResult(
            task_id=task_id,
            status="succeeded",
            video_url=f"cvg-fake://video/{duration}/{resolution}/{token}.mp4",
            model=self.video_model,
            duration_seconds=int(duration),
            ratio="9:16",
            resolution=resolution,
            generate_audio=True,
        )

    def cancel_video_task(self, task_id: str) -> VideoTaskResult:
        return VideoTaskResult(task_id=task_id, status="cancelled")

    def list_video_tasks(
        self,
        *,
        model: str,
        page_size: int = 100,
    ) -> tuple[VideoTaskResult, ...]:
        del model, page_size
        return ()

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
        reference_paths: tuple[Path, ...] = (),
        reference_labels: tuple[str, ...] = (),
    ) -> VideoDiagnosticResult:
        return VideoDiagnosticResult(
            identity_ok=True,
            identity_assessment="consistent",
            style_ok=True,
            constraints_ok=True,
            narrative_order_ok=True,
            confidence=0.95,
            violations=(),
            evidence=tuple(
                {
                    "timestamp": f"{index}s",
                    "object": "shot",
                    "observation": "local fake review frame available",
                    "relationError": None,
                }
                for index, _path in enumerate(frame_paths)
            ),
            shot_boundaries_seconds=(0.0, 4.0, 8.0),
            response_id=f"fake-video-review-{uuid.uuid4().hex[:10]}",
            model=self.review_model,
            request_hash=_request_hash(
                {
                    "prompt": prompt,
                    "references": [str(path) for path in reference_paths],
                    "referenceLabels": list(reference_labels),
                    "frames": [str(path) for path in frame_paths],
                }
            ),
        )

    def diagnose_image(
        self,
        *,
        prompt: str,
        image_path: Path,
    ) -> ImageDiagnosticResult:
        return ImageDiagnosticResult(
            identity_ok=True,
            style_ok=True,
            constraints_ok=True,
            confidence=0.95,
            violations=(),
            evidence=(
                {
                    "object": "anchor",
                    "observation": "local fake anchor available",
                    "relationError": None,
                },
            ),
            response_id=f"fake-image-review-{uuid.uuid4().hex[:10]}",
            model=self.review_model,
            request_hash=_request_hash({"prompt": prompt, "image": str(image_path)}),
        )

    @staticmethod
    def _structured_payload(output_name: str, prompt: str) -> dict[str, Any]:
        if output_name == "StoryExpansionOutput":
            return {
                "expandedStory": (
                    "春日里，小孩准备和灰白猫完成一件轻松的生活小事。猫咪先发现目标并给出自然反应，"
                    "小孩负责需要手部操作的步骤；两者在连续的动作和反馈中完成目标，最后停在温和、"
                    "清楚且便于继续创作的结果状态。"
                ),
                "creativeSummary": "围绕一个生活目标建立起因、互动、结果和治愈收尾。",
                "unresolvedQuestions": [],
            }
        if output_name == "StoryDiagnosisOutput":
            return {
                "overallAssessment": (
                    "核心生活事件清楚，可以通过统一道具流向和动作起点提高可生成性。"
                ),
                "issues": [
                    {
                        "category": "generation_clarity",
                        "evidence": "原稿对部分动作的起点和完成状态描述较少。",
                        "impact": "生成时可能出现动作跳变或道具位置不连续。",
                        "suggestion": "在重写稿中明确起始位置、接触路径和稳定结果。",
                    }
                ],
                "rewriteOptions": [
                    {
                        "strategy": "conservative",
                        "title": "保守修订",
                        "summary": "保持事件顺序，只补充动作和道具连续性。",
                        "tradeoffs": "变化最少，创作增强较弱。",
                    },
                    {
                        "strategy": "balanced",
                        "title": "平衡优化",
                        "summary": "压缩重复动作并加强人猫因果互动。",
                        "tradeoffs": "会调整部分动作顺序。",
                    },
                    {
                        "strategy": "creative",
                        "title": "创作增强",
                        "summary": "重新组织事件节奏并增加温和笑点。",
                        "tradeoffs": "对原稿改动较大。",
                    },
                ],
            }
        if output_name == "StoryRewriteOutput":
            return {
                "rewrittenStory": (
                    "春日上午，小孩和灰白猫在明亮的生活空间里准备完成当天的小目标。猫咪先靠近目标"
                    "观察并引起小孩注意，小孩随后按合理顺序完成拿取、整理或穿戴等手部操作。关键道具"
                    "始终从明确位置移动到明确结果位置，猫咪用自然四足动作参与并给出反馈。最后，小孩"
                    "与猫咪看着完成的成果相视一笑，画面停在稳定的温暖状态。"
                ),
                "changeSummary": ["统一动作起点与道具流向", "加强人猫互动和稳定收尾"],
                "unresolvedQuestions": [],
            }
        if output_name == "VisualAssetPlanOutput":
            return {
                "overallAssessment": "建议只准备会跨片段复用或影响动作可行性的视觉资产。",
                "suggestions": [
                    {
                        "suggestionKey": "shared-environment",
                        "displayName": "场景空环境",
                        "purpose": "environment",
                        "targetScope": "scene",
                        "rationale": "固定空间结构、家具位置和自然光方向。",
                        "prompt": "生成无人物、无猫咪的场景空环境，固定主要家具、出入口和自然光。",
                        "referenceAssetIds": [],
                    },
                    {
                        "suggestionKey": "key-prop",
                        "displayName": "跨片段关键道具",
                        "purpose": "prop",
                        "targetScope": "project",
                        "rationale": "关键道具会在多个片段持续出现，需要保持结构和颜色。",
                        "prompt": (
                            "生成结构完整、无遮挡的关键道具设计图，保持尺寸、颜色和图案清楚。"
                        ),
                        "referenceAssetIds": [],
                    },
                ],
                "textOnlyItems": ["普通胶带、纸张和一次性小物只需文字描述"],
            }
        if output_name == "ShotSuggestionOutput":
            match = re.search(r"严格输出(\d+)个视频片段", prompt)
            count = max(1, min(6, int(match.group(1)) if match else 1))
            shots = []
            for index in range(1, count + 1):
                first = index == 1
                shots.append(
                    {
                        "title": f"生活微事件 {index}",
                        "direction": (
                            "1. 中景固定机位，猫咪位于人物前侧观察目标，人物在后方准备。\n"
                            "2. 近景轻微跟随，猫咪自然靠近，人物完成必要的手部操作。\n"
                            "3. 中景固定收尾，人猫完成同一微事件，环境声和接触声同步。"
                        ),
                        "suggestedDurationSeconds": min(12, 9 + index),
                        "anchorMode": "generate" if first else "text_only",
                        "sceneLookUsage": "derive_anchor" if first else "appearance_only",
                    }
                )
            return {
                "sceneTitle": "治愈生活场景",
                "lookPlan": {
                    "personWardrobe": "浅色舒适日常服装",
                    "personAccessories": "无额外配件",
                    "catAppearance": "保持 Canon 灰白猫身份，不增加无关装饰",
                    "keyProps": "只保留剧情必需的关键道具",
                    "environmentStyle": "indoor",
                    "personPose": "自然准备姿态",
                    "catPose": "自然四足站立或坐姿",
                    "composition": "人物与猫咪空间关系清楚，关键道具完整可见",
                    "additionalInstructions": "保持二维日系治愈画风",
                    "imageRecommended": True,
                    "recommendationReason": "多个片段需要共享服装、环境和关键道具基线",
                },
                "shots": shots,
            }
        raise ValueError(f"fake provider has no fixture for {output_name}")
