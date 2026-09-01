"""Serve deterministic AIGC canvas data for local visual review only."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

app = FastAPI()
ROOT = Path(__file__).resolve().parents[1]

PRODUCT_IMAGES = (
    "/api/v1/preview-assets/product-candidates/candidate-1.png",
    "/api/v1/preview-assets/product-candidates/candidate-2.jpg",
    "/api/v1/preview-assets/product-candidates/candidate-3.jpg",
    "/api/v1/preview-assets/product-candidates/candidate-4.webp",
)
PROMOTED_NODES: list[dict[str, Any]] = []
PROMOTED_EDGES: list[dict[str, str]] = []
RECIPE_REVISIONS: dict[str, dict[str, Any]] = {}
GENERATION_CONFIGS: dict[str, dict[str, Any]] = {}
FILMSTRIP_ERRORS: dict[int, str] = {}
FILMSTRIP_ROOT = ROOT / "var" / "preview-filmstrip" / "demo-video"
DEMO_VIDEO_PATH = ROOT / "design-qa-assets" / "product-ad-demo.mp4"
DEMO_VIDEO_DURATION_MS = 12_000


def _node(
    node_id: str,
    node_type: str,
    object_type: str,
    x: int,
    y: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "objectType": object_type,
        "objectId": node_id,
        "position": {"x": x, "y": y},
        "data": data,
    }


def _edge(
    source: str, source_type: str, source_port: str, target: str, target_type: str, target_port: str
) -> dict[str, str]:
    return {
        "id": f"{source}-{target}",
        "sourceNodeId": source,
        "sourceNodeType": source_type,
        "sourcePort": source_port,
        "targetNodeId": target,
        "targetNodeType": target_type,
        "targetPort": target_port,
    }


def story_canvas(project_id: str) -> dict[str, Any]:
    nodes = [
        _node(
            "brief-1",
            "BriefNode",
            "story_brief",
            60,
            80,
            {
                "title": "创意简报",
                "theme": "暴雨前，小满和橘猫一起抢救天台画作",
                "genre": "治愈冒险",
                "targetDurationSeconds": 60,
                "aspectRatio": "9:16",
            },
        ),
        _node(
            "subject-1",
            "SubjectNode",
            "subject",
            60,
            310,
            {
                "title": "小满",
                "name": "小满",
                "kind": "person",
                "role": "protagonist",
                "identityAnchors": ["12 岁短发女孩，黄色雨衣，红色帆布鞋"],
            },
        ),
        _node(
            "subject-2",
            "SubjectNode",
            "subject",
            60,
            520,
            {
                "title": "团团",
                "name": "团团",
                "kind": "animal",
                "role": "co_protagonist",
                "identityAnchors": ["胖橘猫，左耳缺一小角，绿色项圈"],
            },
        ),
        _node("planner", "StoryPlannerNode", "story_planner", 390, 245, {"title": "三案故事策划"}),
        _node(
            "story-1",
            "StoryCandidateNode",
            "story_revision",
            720,
            55,
            {
                "title": "《雨落之前》",
                "strategy": "关系情感型",
                "status": "candidate",
                "logline": "一人一猫在暴雨前完成一次无声协作。",
                "candidatePromptId": "prompt-plan-1",
                "criticPromptId": "prompt-critic-1",
                "scorecard": {"average": 8.8},
            },
        ),
        _node(
            "story-2",
            "StoryCandidateNode",
            "story_revision",
            720,
            320,
            {
                "title": "《天台营救》",
                "strategy": "问题解决型",
                "status": "candidate",
                "logline": "画作被风卷走，团团发现了更短的救援路线。",
                "candidatePromptId": "prompt-plan-2",
                "criticPromptId": "prompt-critic-2",
                "scorecard": {"average": 8.4},
            },
        ),
        _node(
            "story-3",
            "StoryCandidateNode",
            "story_revision",
            720,
            585,
            {
                "title": "《猫先知道》",
                "strategy": "反转钩子型",
                "status": "approved",
                "logline": "大家以为猫在捣乱，最后发现它提前预警了暴雨。",
                "candidatePromptId": "prompt-plan-3",
                "criticPromptId": "prompt-critic-3",
                "scorecard": {"average": 9.2},
            },
        ),
        _node(
            "approval", "ApprovalGateNode", "story_approval", 1050, 320, {"title": "人工故事定稿"}
        ),
        _node(
            "director",
            "StoryboardDirectorNode",
            "storyboard_director",
            1360,
            320,
            {"title": "60 秒分镜编译"},
        ),
        _node("scene-1", "SceneNode", "scene", 1670, 130, {"title": "风起天台", "order": 1}),
        _node("scene-2", "SceneNode", "scene", 1670, 480, {"title": "雨前抢救", "order": 2}),
        _node(
            "beat-1",
            "ShotBeatNode",
            "shot_beat",
            1980,
            90,
            {
                "title": "乌云压近",
                "action": "团团踩住被风掀起的画纸，小满冲向画架。",
                "camera": "低机位推进至猫爪特写",
                "dialogue": "小满：团团，别松开！",
                "durationSeconds": 8,
                "promptId": "prompt-storyboard",
            },
        ),
        _node(
            "beat-2",
            "ShotBeatNode",
            "shot_beat",
            1980,
            350,
            {
                "title": "接力收画",
                "action": "一人一猫沿画架逐张固定画作。",
                "camera": "中景横移接手部特写",
                "dialogue": "",
                "durationSeconds": 22,
                "promptId": "prompt-storyboard",
            },
        ),
        _node(
            "beat-3",
            "ShotBeatNode",
            "shot_beat",
            1980,
            610,
            {
                "title": "第一滴雨",
                "action": "最后一幅画入箱，雨滴落在绿色项圈上。",
                "camera": "微距慢推后拉远",
                "dialogue": "小满：你早就知道，对吧？",
                "durationSeconds": 30,
                "promptId": "prompt-storyboard",
            },
        ),
    ]
    edges = [
        _edge("brief-1", "BriefNode", "brief", "planner", "StoryPlannerNode", "brief"),
        _edge("subject-1", "SubjectNode", "subject[]", "planner", "StoryPlannerNode", "subject[]"),
        _edge("subject-2", "SubjectNode", "subject[]", "planner", "StoryPlannerNode", "subject[]"),
        *[
            _edge(
                "planner",
                "StoryPlannerNode",
                "story_revision",
                f"story-{index}",
                "StoryCandidateNode",
                "story_revision",
            )
            for index in range(1, 4)
        ],
        *[
            _edge(
                f"story-{index}",
                "StoryCandidateNode",
                "story_revision",
                "approval",
                "ApprovalGateNode",
                "story_revision",
            )
            for index in range(1, 4)
        ],
        _edge(
            "approval",
            "ApprovalGateNode",
            "story_revision",
            "director",
            "StoryboardDirectorNode",
            "story_revision",
        ),
        _edge(
            "director", "StoryboardDirectorNode", "scene_plan", "scene-1", "SceneNode", "scene_plan"
        ),
        _edge(
            "director", "StoryboardDirectorNode", "scene_plan", "scene-2", "SceneNode", "scene_plan"
        ),
        _edge("scene-1", "SceneNode", "shot_beat[]", "beat-1", "ShotBeatNode", "shot_beat[]"),
        _edge("scene-2", "SceneNode", "shot_beat[]", "beat-2", "ShotBeatNode", "shot_beat[]"),
        _edge("scene-2", "SceneNode", "shot_beat[]", "beat-3", "ShotBeatNode", "shot_beat[]"),
    ]
    return {
        "projectId": project_id,
        "canvasV2Enabled": True,
        "templateKey": "short_drama",
        "featureFlags": {
            "universalCanvas": True,
            "productAdTemplate": False,
            "videoEditV2": True,
        },
        "layoutVersion": 3,
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0, "y": 0, "zoom": 0.64},
        "syncStatus": "saved",
    }


def healing_recipe_instance(instance_id: str = "recipe-healing-demo") -> dict[str, Any]:
    episode_rules = {
        "personWardrobe": "米白针织衫、苔绿色背带裤与棕色软底鞋",
        "timeWeather": "初秋雨后傍晚，窗外有柔和余晖",
        "mainScene": "有木桌与低窗台的水彩厨房",
        "environment": "indoor",
        "coreProps": ["小木碗", "三颗栗子", "亚麻餐巾"],
        "catBehaviorMode": "natural",
        "soundPlan": {
            "ambient": ["雨后屋檐滴水", "远处晚鸟"],
            "foley": ["栗子轻碰木碗", "猫爪踩过餐巾"],
            "musicMood": "很轻的木吉他与钟琴，温暖但不煽情",
            "dialoguePolicy": "none",
        },
        "stylePositive": ["柔和水彩晕染", "纸张纤维质感", "低饱和暖色"],
        "styleExcluded": ["准写实线稿", "塑料三维质感", "高反差霓虹"],
        "canonProfileId": "healing_child_cat_canon_v2",
    }

    durations = (11, 10, 10)
    shot_titles = ("把栗子放进木碗", "团团发现滚落的栗子", "一起守着窗边晚霞")
    actions = (
        ("小满把木碗放到窗边", "她逐颗放入栗子，团团在脚边闻一闻", "最后一颗栗子安稳落下"),
        ("一颗栗子沿桌面慢慢滚动", "团团四足追过去，用前爪轻轻拦住", "小满把栗子拾回碗中"),
        ("小满铺好亚麻餐巾", "团团蜷在窗台边，尾巴缓慢摆动", "一人一猫安静看着雨后晚霞"),
    )
    shots: list[dict[str, Any]] = []
    for index, (duration, title, shot_actions) in enumerate(
        zip(durations, shot_titles, actions, strict=True),
        1,
    ):
        first_end = round(duration / 3, 2)
        second_end = round(duration * 2 / 3, 2)
        anchor_id = f"recipe-anchor-{index}"
        video_candidates: list[dict[str, Any]] = []
        selected_video_id: str | None = None
        if index == 1:
            selected_video_id = "demo-video"
            video_candidates.append(
                {
                    "id": "demo-video",
                    "sha256": "preview-video-approved",
                    "status": "approved",
                    "mediaType": "video",
                    "contentUrl": "/api/v1/assets/demo-video/content",
                    "qc": {"hasAudio": True, "durationMs": 11000},
                    "diagnosticStatus": "passed",
                    "diagnostics": [{"status": "passed", "frameCount": 8}],
                }
            )
        elif index == 2:
            video_candidates.append(
                {
                    "id": "recipe-video-review-2",
                    "sha256": "preview-video-needs-review",
                    "status": "content_review",
                    "mediaType": "video",
                    "contentUrl": "/api/v1/assets/demo-video/content",
                    "qc": {"hasAudio": True, "durationMs": 10000},
                    "diagnosticStatus": "failed",
                    "diagnostics": [
                        {
                            "status": "failed",
                            "frameCount": 8,
                            "issues": ["6.2 秒处猫咪短暂出现人形前肢"],
                        }
                    ],
                }
            )
        shots.append(
            {
                "beatId": f"recipe-beat-{index}",
                "shotId": f"recipe-shot-{index}",
                "title": title,
                "durationSeconds": duration,
                "status": "approved" if selected_video_id else "planned",
                "temporalBeats": [
                    {
                        "phase": "start",
                        "startSecond": 0,
                        "endSecond": first_end,
                        "childAction": shot_actions[0],
                    },
                    {
                        "phase": "change",
                        "startSecond": first_end,
                        "endSecond": second_end,
                        "childAction": shot_actions[1],
                    },
                    {
                        "phase": "warm_close",
                        "startSecond": second_end,
                        "endSecond": duration,
                        "childAction": shot_actions[2],
                    },
                ],
                "selectedAnchorAssetId": anchor_id,
                "selectedVideoAssetId": selected_video_id,
                "anchorCandidates": [
                    {
                        "id": anchor_id,
                        "sha256": f"preview-anchor-{index}",
                        "status": "approved",
                        "mediaType": "image",
                        "contentUrl": "/api/v1/preview-assets/person.png",
                        "qc": {"width": 720, "height": 1280},
                        "diagnosticStatus": "passed",
                        "diagnostics": [
                            {
                                "status": "passed",
                                "checks": ["child_identity", "cat_identity", "watercolor_style"],
                            }
                        ],
                    }
                ],
                "videoCandidates": video_candidates,
            }
        )

    return {
        "id": instance_id,
        "projectId": "project-healing-recipe",
        "recipeKey": "healing_child_cat_v1",
        "recipeVersion": 1,
        "revision": 7,
        "theme": "雨停以后，小满和团团一起收好三颗栗子",
        "inspirationKey": "weather",
        "targetDurationSeconds": 31,
        "qualityTier": "balanced",
        "canonProfileId": "healing_child_cat_canon_v2",
        "stage": "video",
        "shotDurations": list(durations),
        "currentBlocker": "镜头 2 的视频专项诊断未通过：请退回重做、局部重编，或填写理由人工覆盖。",
        "primaryAction": "处理镜头 2 的诊断问题",
        "estimatedCostMicros": 326000,
        "reviewStages": [
            {"key": "story", "complete": True},
            {"key": "anchors", "complete": True},
            {"key": "video", "complete": False},
            {"key": "sequence", "complete": False},
        ],
        "progress": {
            "storyApproved": True,
            "episodeRulesLocked": True,
            "shotCount": 3,
            "approvedAnchorCount": 3,
            "approvedVideoCount": 1,
            "sequenceReady": False,
            "finalApproved": False,
        },
        "episodeRules": episode_rules,
        "storyCandidates": [
            {
                "id": "recipe-story-approved",
                "revision": 1,
                "strategy": "低压力小发现",
                "status": "approved",
                "title": "《第三颗栗子》",
                "logline": "一颗滚落的栗子，让小满和团团在雨后共享了一次安静的小小协作。",
                "synopsis": (
                    "小满收拾雨后捡来的栗子，团团拦住滚向桌边的一颗。"
                    "没有对白，只有细小动作和窗外余晖。"
                ),
                "episodeRules": episode_rules,
                "scoreAverage": 9.1,
                "scoreRationale": "事件轻、动作清楚，猫咪参与自然，结尾留有安静余味。",
            }
        ],
        "shots": shots,
        "sequenceCandidate": None,
    }


def healing_recipe_canvas(project_id: str) -> dict[str, Any]:
    recipe = healing_recipe_instance()
    nodes = [
        _node(
            recipe["id"],
            "RecipeGroupNode",
            "production_recipe_instance",
            90,
            160,
            {"title": "一人一猫治愈短片", **recipe},
        ),
        _node(
            "recipe-brief",
            "BriefNode",
            "story_brief",
            520,
            40,
            {
                "title": "创意与故事",
                "theme": recipe["theme"],
                "targetDurationSeconds": 31,
                "aspectRatio": "9:16",
                "genre": "治愈日常",
            },
        ),
        _node(
            "recipe-child",
            "SubjectNode",
            "subject",
            520,
            250,
            {
                "title": "小满 · Canon-v2",
                "kind": "person",
                "role": "protagonist",
                "identityAnchors": ["固定儿童身份与比例参考"],
            },
        ),
        _node(
            "recipe-cat",
            "SubjectNode",
            "subject",
            520,
            460,
            {
                "title": "团团 · Canon-v2",
                "kind": "animal",
                "role": "co_protagonist",
                "identityAnchors": ["固定猫咪身份，自然四足结构"],
            },
        ),
    ]
    for index, shot in enumerate(recipe["shots"], 1):
        nodes.append(
            _node(
                shot["beatId"],
                "ShotBeatNode",
                "shot_beat",
                900,
                40 + (index - 1) * 250,
                {
                    "title": shot["title"],
                    "durationSeconds": shot["durationSeconds"],
                    "action": shot["temporalBeats"][1]["childAction"],
                    "camera": "克制的缓慢推进，保持水彩纸张质感",
                    "dialogue": "",
                    "temporalBeats": shot["temporalBeats"],
                },
            )
        )
    return {
        "projectId": project_id,
        "canvasV2Enabled": True,
        "templateKey": "short_drama",
        "featureFlags": {"universalCanvas": True, "productAdTemplate": False, "videoEditV2": True},
        "layoutVersion": 7,
        "nodes": nodes,
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 0.72},
        "syncStatus": "saved",
    }


def product_canvas(project_id: str) -> dict[str, Any]:
    candidates = [
        {
            "id": f"candidate-{index}",
            "assetId": f"candidate-asset-{index}",
            "title": title,
            "thumbnailUrl": url,
            "promptId": f"prompt-product-{index}",
            "status": "candidate",
        }
        for index, (title, url) in enumerate(
            zip(
                ("冰面英雄构图", "水花动势构图", "冰块与杯体场景", "蓝色极简棚拍"),
                PRODUCT_IMAGES,
                strict=True,
            ),
            1,
        )
    ]
    nodes = [
        _node(
            "product-reference",
            "ReferenceAssetNode",
            "asset",
            70,
            90,
            {
                "title": "产品主体 · 蓝色罐装饮料",
                "assetId": "product-reference-asset",
                "thumbnailUrl": PRODUCT_IMAGES[2],
                "semanticRole": "包装正面 / 标签细节 / 材质",
                "status": "ready",
            },
        ),
        _node(
            "talent-reference",
            "ReferenceAssetNode",
            "asset",
            70,
            350,
            {
                "title": "模特与尺寸关系参考",
                "assetId": "talent-reference-asset",
                "thumbnailUrl": "/api/v1/preview-assets/person.png",
                "semanticRole": "人物身份 / 全身比例",
                "status": "ready",
            },
        ),
        _node(
            "image-batch",
            "GenerationBatchNode",
            "media_generation_batch",
            430,
            180,
            {
                "title": "产品图片生成批次",
                "prompt": (
                    "电影级蓝色冰爽广告，产品包装与标签保持准确；"
                    "分别探索英雄构图、水花动势、冰块场景与极简棚拍。"
                ),
                "candidateCount": 4,
                "status": "awaiting_selection",
                "candidates": candidates,
            },
        ),
        _node(
            "selected-image",
            "ImageAssetNode",
            "asset",
            830,
            180,
            {
                "title": "已提升候选 · 冰面英雄构图",
                "assetId": "selected-image-asset",
                "thumbnailUrl": PRODUCT_IMAGES[0],
                "mediaType": "image",
                "promptId": "prompt-product-1",
                "status": "approved",
            },
        ),
        _node(
            "video-generation",
            "VideoGenerationNode",
            "video_generation_stage",
            1190,
            180,
            {
                "title": "图生视频",
                "status": "succeeded",
                "promptId": "prompt-video-generation",
            },
        ),
        _node(
            "video-asset",
            "VideoAssetNode",
            "asset",
            1540,
            160,
            {
                "title": "冰爽产品广告 · 12 秒",
                "assetId": "demo-video",
                "contentUrl": "/api/v1/assets/demo-video/content?v=2",
                "posterUrl": PRODUCT_IMAGES[0],
                "durationMs": 12000,
                "defaultEditStartMs": 3600,
                "defaultEditEndMs": 7600,
                "promptId": "prompt-video-generation",
                "status": "approved",
            },
        ),
        _node(
            "video-edit",
            "VideoEditNode",
            "video_edit_stage",
            1900,
            160,
            {
                "title": "局部重编配方",
                "instruction": "选择 0.5–13 秒区间，用视觉标注和主体参考生成新版本。",
                "revision": 1,
                "status": "ready",
            },
        ),
        _node(
            "review",
            "ReviewNode",
            "asset_review_stage",
            2260,
            160,
            {"title": "人工审核", "status": "awaiting_asset"},
        ),
        _node(
            "timeline",
            "TimelineNode",
            "timeline_stage",
            2620,
            160,
            {"title": "广告时间线", "status": "ready"},
        ),
        *PROMOTED_NODES,
    ]
    edges = [
        _edge(
            "product-reference",
            "ReferenceAssetNode",
            "media_reference[]",
            "image-batch",
            "GenerationBatchNode",
            "media_reference[]",
        ),
        _edge(
            "talent-reference",
            "ReferenceAssetNode",
            "media_reference[]",
            "image-batch",
            "GenerationBatchNode",
            "media_reference[]",
        ),
        _edge(
            "image-batch",
            "GenerationBatchNode",
            "image_asset[]",
            "selected-image",
            "ImageAssetNode",
            "image_asset[]",
        ),
        _edge(
            "selected-image",
            "ImageAssetNode",
            "image_asset",
            "video-generation",
            "VideoGenerationNode",
            "image_asset",
        ),
        _edge(
            "video-generation",
            "VideoGenerationNode",
            "video_asset",
            "video-asset",
            "VideoAssetNode",
            "video_asset",
        ),
        _edge(
            "video-asset",
            "VideoAssetNode",
            "video_asset",
            "video-edit",
            "VideoEditNode",
            "video_asset",
        ),
        _edge(
            "video-asset", "VideoAssetNode", "video_asset", "review", "ReviewNode", "video_asset"
        ),
        _edge(
            "review", "ReviewNode", "approved_asset", "timeline", "TimelineNode", "approved_asset"
        ),
        *PROMOTED_EDGES,
    ]
    return {
        "projectId": project_id,
        "canvasV2Enabled": True,
        "templateKey": "product_ad",
        "featureFlags": {
            "universalCanvas": True,
            "productAdTemplate": True,
            "videoEditV2": True,
        },
        "layoutVersion": 8,
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0, "y": 0, "zoom": 0.52},
        "syncStatus": "saved",
    }


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {
        "ready": True,
        "databaseReady": True,
        "contractVersion": 5,
        "alembicRevision": "0022_healing_child_cat_recipe",
        "expectedAlembicRevision": "0022_healing_child_cat_recipe",
    }


@app.get("/api/v1/runtime-settings")
def runtime_settings() -> dict[str, Any]:
    config = {
        "planningModel": "doubao-seed-2-1-pro",
        "imageModel": "seedream-5",
        "videoModel": "seedance-2",
        "reviewModel": "doubao-seed-2-1-pro",
        "videoResolution": "720p",
        "semanticReviewEnabled": True,
        "revision": 1,
        "updatedAt": None,
        "usingOverride": False,
    }
    return {
        "current": config,
        "deploymentDefaults": config,
        "modelCatalog": [],
        "arkApiKeyConfigured": True,
        "arkReady": True,
        "ffmpegAvailable": True,
        "ffprobeAvailable": True,
        "databaseReady": True,
        "videoGenerationReady": True,
        "localCompositionReady": True,
        "databaseManagedSeparately": True,
        "diagnostics": {
            "provider": "ark",
            "arkBaseUrlProfile": "standard",
            "directorRequestTimeoutSeconds": 240,
            "reviewRequestTimeoutSeconds": 240,
            "videoApiTimeoutSeconds": 120,
            "pollIntervalSeconds": 10,
            "taskTimeoutSeconds": 1800,
            "imageRequestTimeoutSeconds": 600,
            "workRoot": "var/work",
            "assetRoot": "var/assets",
            "configurationWarnings": [],
            "configurationIssues": [],
        },
    }


@app.get("/api/v1/projects")
def projects() -> list[dict[str, str]]:
    return [
        {
            "id": "project-healing-recipe",
            "title": "第三颗栗子 · 一人一猫治愈短片",
            "contentDate": "2026-08-20",
            "status": "active",
        },
        {
            "id": "project-demo",
            "title": "冰爽一刻 · 产品广告",
            "contentDate": "2026-08-20",
            "status": "active",
        },
        {
            "id": "project-story",
            "title": "猫先知道",
            "contentDate": "2026-08-20",
            "status": "active",
        },
    ]


@app.get("/api/v1/task-center")
def task_center() -> dict[str, list[Any]]:
    return {"runtimeJobs": [], "persistentTasks": []}


@app.get("/api/v2/provider-capabilities")
def provider_capabilities(
    media_kind: str | None = Query(default=None, alias="mediaKind"),
) -> list[dict[str, Any]]:
    kind = media_kind or "video"
    is_video = kind in {"video", "video_edit"}
    capabilities = {
        "provider": "ark",
        "model": "doubao-seedance-2-0-260128" if is_video else "doubao-seedream-5-0",
        "modes": ["text_to_video", "image_to_video"] if is_video else ["text_to_image"],
        "aspectRatios": ["9:16", "16:9", "1:1"],
        "resolutions": ["480p", "720p", "1080p"] if is_video else ["1k", "2k"],
        "durations": [5, 8, 12] if is_video else [1],
        "candidateCounts": [1, 2, 4],
        "audio": is_video,
        "maxReferenceImages": 4 if is_video else 8,
        "estimatedCostMicros": 135000 if is_video else 28000,
        "cameraMotions": [
            {"value": "static", "label": "固定镜头", "enabled": True},
            {"value": "follow", "label": "跟随拍摄", "enabled": True},
            {"value": "pan_left", "label": "镜头左摇", "enabled": True},
            {"value": "pan_right", "label": "镜头右摇", "enabled": True},
            {"value": "tilt_up", "label": "镜头上摇", "enabled": True},
            {"value": "tilt_down", "label": "镜头下摇", "enabled": True},
            {"value": "push_in", "label": "推进", "enabled": True},
            {"value": "pull_out", "label": "拉远", "enabled": True},
            {"value": "orbit", "label": "环绕", "enabled": True},
            {"value": "handheld", "label": "手持", "enabled": True},
        ]
        if is_video
        else [],
    }
    return [
        {
            "id": f"preview-{kind}-capability",
            "provider": capabilities["provider"],
            "model": capabilities["model"],
            "mediaKind": kind,
            "capabilities": capabilities,
            "active": True,
        }
    ]


@app.put("/api/v2/canvas/nodes/{node_id}/generation-config")
def save_generation_config(
    node_id: str,
    payload: dict[str, Any] = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    revision = int(if_match or GENERATION_CONFIGS.get(node_id, {}).get("revision", 0)) + 1
    saved = {
        "id": f"preview-generation-config-{node_id}",
        "nodeId": node_id,
        "revision": revision,
        **payload,
    }
    GENERATION_CONFIGS[node_id] = saved
    return saved


@app.get("/api/v2/canvas-templates")
def templates() -> list[dict[str, Any]]:
    return [
        {
            "key": "short_drama",
            "title": "AIGC 短剧",
            "description": "从简报和两个以上叙事主体生成故事、分镜与媒体。",
            "defaultCandidateCount": 3,
            "nodeTypes": ["BriefNode", "SubjectNode", "StoryPlannerNode", "TimelineNode"],
        },
        {
            "key": "product_ad",
            "title": "产品广告",
            "description": "从包装与风格参考生成四组产品图候选并继续制作视频。",
            "defaultCandidateCount": 4,
            "nodeTypes": [
                "ReferenceAssetNode",
                "GenerationBatchNode",
                "VideoAssetNode",
                "VideoEditNode",
            ],
        },
        {
            "key": "blank",
            "title": "空白画布",
            "description": "从任意素材节点开始搭建类型化媒体生产图。",
            "defaultCandidateCount": 4,
            "nodeTypes": [],
        },
    ]


@app.get("/api/v2/projects/{project_id}/canvas")
def get_canvas(project_id: str) -> dict[str, Any]:
    if project_id == "project-healing-recipe":
        return healing_recipe_canvas(project_id)
    if project_id == "project-story":
        return story_canvas(project_id)
    return product_canvas(project_id)


@app.get("/api/v2/recipe-instances/{instance_id}")
def get_recipe_instance(instance_id: str) -> dict[str, Any]:
    if instance_id != "recipe-healing-demo":
        raise HTTPException(status_code=404, detail="unknown production recipe preview")
    return healing_recipe_instance(instance_id)


@app.patch("/api/v2/projects/{project_id}/canvas/layout")
def save_layout(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "layoutVersion": int(payload.get("expectedVersion", 8)) + 1,
        "syncStatus": "saved",
        "rebased": True,
    }


@app.post("/api/v2/projects/{project_id}/canvas/nodes")
def create_node(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    node_id = str(uuid.uuid4())
    node = _node(
        node_id,
        str(payload["nodeType"]),
        str(payload["objectType"]),
        830,
        520 + len(PROMOTED_NODES) * 220,
        dict(payload.get("data") or {}),
    )
    node["objectId"] = payload.get("objectId")
    PROMOTED_NODES.append(node)
    return node


@app.post("/api/v2/projects/{project_id}/canvas/edges")
def create_edge(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    edge = {"id": str(uuid.uuid4()), **{key: str(value) for key, value in payload.items()}}
    PROMOTED_EDGES.append(edge)
    return edge


@app.post("/api/v2/generation-batches", status_code=202)
def create_batch(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "candidateCount": payload.get("candidateCount", 4),
    }


@app.post("/api/v2/video-edit-recipes")
def create_recipe(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    recipe_id = str(uuid.uuid4())
    recipe = {
        "id": recipe_id,
        "projectId": payload["projectId"],
        "sourceAssetId": payload["sourceAssetId"],
        "canvasNodeId": "video-edit",
        "revision": 1,
        "startMs": payload["startMs"],
        "endMs": payload["endMs"],
        "instruction": payload["instruction"],
        "referenceAssetIds": payload.get("referenceAssetIds", []),
        "annotations": payload.get("annotations", []),
        "status": "draft",
        "estimatedCostMicros": None,
        "compilation": None,
    }
    RECIPE_REVISIONS[recipe_id] = recipe
    return recipe


@app.patch("/api/v2/video-edit-recipes/{recipe_id}")
def update_recipe(
    recipe_id: str,
    payload: dict[str, Any] = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    current = RECIPE_REVISIONS[recipe_id]
    revision = int(if_match or current["revision"])
    next_recipe = {**current, **payload, "revision": revision + 1, "status": "draft"}
    RECIPE_REVISIONS[recipe_id] = next_recipe
    return next_recipe


@app.put("/api/v2/video-edit-recipes/{recipe_id}/annotations")
def update_annotations(
    recipe_id: str,
    payload: dict[str, Any] = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    current = RECIPE_REVISIONS[recipe_id]
    revision = int(if_match or current["revision"])
    next_recipe = {
        **current,
        "annotations": payload.get("annotations", []),
        "revision": revision + 1,
        "status": "draft",
    }
    RECIPE_REVISIONS[recipe_id] = next_recipe
    return next_recipe


@app.post("/api/v2/video-edit-recipes/{recipe_id}/compile")
def compile_recipe(recipe_id: str) -> dict[str, Any]:
    recipe = RECIPE_REVISIONS[recipe_id]
    actual_references = [
        {
            "assetId": asset_id,
            "subjectRevisionId": None,
            "semanticRole": "video_edit_reference",
            "providerIncluded": True,
            "providerSlot": f"control_anchor_reference_{index}",
            "omissionReason": None,
        }
        for index, asset_id in enumerate(recipe.get("referenceAssetIds", []), 1)
    ]
    plan = {
        "recipeId": recipe_id,
        "mode": "two_stage",
        "stages": [
            {"kind": "control_anchor", "boundary": "start"},
            {"kind": "control_anchor", "boundary": "end"},
            {"kind": "video_edit", "boundary": None},
        ],
        "imageCallCount": 2,
        "videoCallCount": 1,
        "estimatedCostMicros": 128000,
        "warnings": ["当前 Ark 将先生成两个干净控制锚点，再执行区间视频重编"],
        "provider": "ark",
        "model": "seedance-2",
        "actualReferences": actual_references,
    }
    RECIPE_REVISIONS[recipe_id] = {
        **recipe,
        "status": "compiled",
        "estimatedCostMicros": 128000,
        "compilation": plan,
    }
    return plan


@app.post("/api/v2/video-edit-recipes/{recipe_id}/submit", status_code=202)
def submit_recipe(recipe_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {
        "recipeId": recipe_id,
        "jobId": str(uuid.uuid4()),
        "status": "queued",
        "idempotencyKey": payload["idempotencyKey"],
    }


@app.get("/api/v1/preview-assets/person.png")
def person_preview() -> FileResponse:
    return FileResponse(ROOT / "风格定稿" / "人物.png", media_type="image/png")


@app.get("/api/v1/preview-assets/product-candidates/{filename}")
def product_preview(filename: str) -> FileResponse:
    allowed = {
        "candidate-1.png",
        "candidate-2.jpg",
        "candidate-3.jpg",
        "candidate-4.webp",
    }
    if filename not in allowed:
        raise ValueError("unknown product preview asset")
    return FileResponse(ROOT / "design-qa-assets" / "product-candidates" / filename)


@app.get("/api/v1/assets/demo-video/content")
def demo_video() -> FileResponse:
    return FileResponse(
        DEMO_VIDEO_PATH,
        media_type="video/mp4",
    )


def _preview_filmstrip(frame_count: int) -> dict[str, Any]:
    frame_dir = FILMSTRIP_ROOT / str(frame_count)
    frames = tuple(sorted(frame_dir.glob("frame-*.png")))
    timestamps_ms = tuple(
        round(index * (DEMO_VIDEO_DURATION_MS - 100) / (frame_count - 1))
        for index in range(frame_count)
    )
    return {
        "assetId": "demo-video",
        "frameCount": frame_count,
        "status": "failed"
        if frame_count in FILMSTRIP_ERRORS
        else "ready"
        if len(frames) == frame_count
        else "not_requested",
        "stepId": f"preview-filmstrip-demo-video-{frame_count}",
        "error": (
            {"code": "ffmpeg_failed", "message": FILMSTRIP_ERRORS[frame_count]}
            if frame_count in FILMSTRIP_ERRORS
            else None
        ),
        "frames": [
            {
                "assetId": f"demo-video-frame-{frame_count}-{index}",
                "timestampMs": timestamps_ms[index - 1],
                "contentUrl": (
                    f"/api/v1/preview-assets/filmstrip/demo-video/{frame_count}/{index}.png"
                ),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for index, path in enumerate(frames, 1)
        ],
    }


@app.post("/api/v2/assets/demo-video/filmstrip-runs", status_code=202)
def create_demo_filmstrip(
    frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
) -> dict[str, Any]:
    existing = _preview_filmstrip(frame_count)
    if existing["status"] == "ready":
        return existing
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        FILMSTRIP_ERRORS[frame_count] = "FFmpeg 未安装，无法生成真实视频帧带"
        return _preview_filmstrip(frame_count)
    FILMSTRIP_ERRORS.pop(frame_count, None)
    frame_dir = FILMSTRIP_ROOT / str(frame_count)
    frame_dir.mkdir(parents=True, exist_ok=True)
    for path in frame_dir.glob("frame-*.png"):
        path.unlink()
    timestamps_ms = tuple(
        round(index * (DEMO_VIDEO_DURATION_MS - 100) / (frame_count - 1))
        for index in range(frame_count)
    )
    try:
        for index, timestamp_ms in enumerate(timestamps_ms, 1):
            output = frame_dir / f"frame-{index:02d}.png"
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp_ms / 1000:.3f}",
                    "-i",
                    str(DEMO_VIDEO_PATH),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=240:-2",
                    str(output),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if completed.returncode != 0 or not output.is_file():
                detail = completed.stderr.strip() or f"无法抽取 {timestamp_ms}ms 视频帧"
                raise RuntimeError(detail)
    except Exception as exc:
        for path in frame_dir.glob("frame-*.png"):
            path.unlink()
        FILMSTRIP_ERRORS[frame_count] = str(exc)
    return _preview_filmstrip(frame_count)


@app.get("/api/v2/assets/demo-video/filmstrip")
def get_demo_filmstrip(
    frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
) -> dict[str, Any]:
    return _preview_filmstrip(frame_count)


@app.get("/api/v1/preview-assets/filmstrip/demo-video/{frame_count}/{index}.png")
def demo_filmstrip_frame(frame_count: int, index: int) -> FileResponse:
    if not 4 <= frame_count <= 12 or not 1 <= index <= frame_count:
        raise HTTPException(status_code=404, detail="unknown filmstrip frame")
    path = FILMSTRIP_ROOT / str(frame_count) / f"frame-{index:02d}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="filmstrip frame is not ready")
    return FileResponse(path, media_type="image/png")


@app.get("/api/v2/prompt-runs/{prompt_id}")
def prompt_run(prompt_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    is_storyboard = prompt_id == "prompt-storyboard"
    is_product = prompt_id.startswith("prompt-product") or prompt_id == "prompt-video-generation"
    return {
        "id": prompt_id,
        "stepId": f"step-{prompt_id}",
        "purpose": "media_generation"
        if is_product
        else "storyboard_director"
        if is_storyboard
        else "story_candidate",
        "nodeId": "image-batch" if is_product else "storyboard" if is_storyboard else "story-3",
        "businessObjectType": "media_generation_batch"
        if is_product
        else "story_revision"
        if is_storyboard
        else "project",
        "businessObjectId": "image-batch"
        if is_product
        else "story-3"
        if is_storyboard
        else "project-demo",
        "parentRunId": None,
        "templateName": "media.product-ad.candidate.v1"
        if is_product
        else "storyboard.director"
        if is_storyboard
        else "story.reverse_hook",
        "templateVersion": "1.0.0",
        "systemPrompt": (
            "你是商业产品摄影导演。严格保持包装、商标、标签文字、材质和比例。"
            if is_product
            else "你是AIGC短剧分镜导演。每个 Beat 只能包含一个连续动作意图。"
            if is_storyboard
            else "你是竖屏短剧故事策划师。严格输出可拍摄、强因果的结构化故事。"
        ),
        "userPrompt": (
            "以蓝色罐装饮料为英雄产品，制作电影级冰爽广告候选；当前候选探索独立构图。"
            if is_product
            else "把已批准的《猫先知道》拆成 4 至 7 个 Beat，覆盖两个场景。"
            if is_storyboard
            else ("主题：暴雨前抢救天台画作。主体：小满、团团。目标时长：60 秒。策略：反转钩子型。")
        ),
        "finalPrompt": (
            "[system]\n你是商业产品摄影导演。\n[user]\n严格锁定产品包装；生成独立候选；冰块、水花与蓝色棚拍光。"
            if is_product
            else "[system]\n你是AIGC短剧分镜导演。\n[user]\n"
            "把批准故事拆成可独立编辑的 Beat；总时长严格为 60 秒。"
            if is_storyboard
            else (
                "[system]\n你是竖屏短剧故事策划师。\n[user]\n"
                "主题：暴雨前抢救天台画作；两个主体都必须推动情节；60 秒。"
            )
        ),
        "providerInternalTransform": "not_observable",
        "providerRequestSnapshot": {"model": "doubao-seed-2-1-pro", "temperature": 0.7},
        "inputSnapshot": {"subjectRevisionIds": ["subject-1-r1", "subject-2-r1"]},
        "provider": "ark",
        "model": "doubao-seed-2-1-pro",
        "parameters": {"temperature": 0.7},
        "rawResponse": {"requestId": "demo-request"},
        "structuredResponse": (
            {"candidate": prompt_id, "assetId": "selected-image-asset"}
            if is_product
            else {"beats": ["乌云压近", "接力收画", "第一滴雨"]}
            if is_storyboard
            else {"title": "猫先知道"}
        ),
        "acceptedResponse": (
            {"candidate": prompt_id, "assetId": "selected-image-asset"}
            if is_product
            else {"beats": ["乌云压近", "接力收画", "第一滴雨"]}
            if is_storyboard
            else {"title": "猫先知道"}
        ),
        "responseDiff": {},
        "tokenUsage": {"input": 1120, "output": 684},
        "costMicros": 2400,
        "durationMs": 5230,
        "status": "succeeded",
        "error": None,
        "inputHash": "demo-input-sha256",
        "outputHash": "demo-output-sha256",
        "retryChain": [],
        "createdAt": now,
        "completedAt": now,
    }


@app.get("/api/v2/projects/{project_id}/events")
async def events(project_id: str) -> StreamingResponse:
    async def stream():
        while True:
            yield f": keepalive {project_id}\n\n"
            await asyncio.sleep(15)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/{spa_path:path}", include_in_schema=False)
def web_app(spa_path: str) -> FileResponse:
    """Serve the built SPA so browser QA needs only this deterministic preview process."""
    dist_root = (ROOT / "web" / "dist").resolve()
    requested = (dist_root / spa_path).resolve()
    try:
        requested.relative_to(dist_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown preview path") from exc
    if requested.is_file():
        return FileResponse(requested)
    index = dist_root / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="run the web build before visual review")
    return FileResponse(index, media_type="text/html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
