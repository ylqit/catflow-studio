from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from catflow.application.provider_config import ProviderRuntime
from catflow.application.series import SeriesPlanDraft
from catflow.application.service import StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app

WRITE_HEADERS = {
    "Origin": "http://127.0.0.1:8877",
    "X-CatFlow-CSRF": "series-csrf",
}


def _client() -> tuple[StudioService, TestClient]:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="planning-model",
            image_model="image-model",
            video_model="video-model",
            diagnostic_model="diagnostic-model",
            capability_revision="series-capability-v1",
            paid_calls_enabled=True,
            maximum_video_references=8,
        ),
    )
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="series-csrf",
            worker_ready=True,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
        ),
    )
    return service, TestClient(app)


def _series_payload() -> dict[str, object]:
    return {
        "title": "森林野餐",
        "premise": "孩子和猫咪从准备野餐到返程",
        "narrativeMode": "continuous",
        "plannedEpisodeCount": 2,
        "defaultEpisodeDurationSeconds": 12,
        "worldSetting": "家和森林",
        "emotionalDirection": "期待到满足",
        "recurringElements": ["野餐篮"],
        "mustKeep": ["同一位孩子"],
        "mustAvoid": ["危险动作"],
    }


def _plan_payload() -> dict[str, object]:
    episodes = []
    for order, title in ((1, "准备野餐"), (2, "快乐野餐")):
        episodes.append(
            {
                "order": order,
                "title": title,
                "targetDurationSeconds": 12,
                "premise": title,
                "openingState": f"第 {order} 集开场",
                "trigger": "孩子开始行动",
                "childIntent": "完成本集事件",
                "childAction": "从初始位置完成动作后停下",
                "catResponse": "猫咪观察后回应并停下",
                "visibleChange": "道具状态发生变化",
                "endingState": f"第 {order} 集结尾",
                "continuityCarryover": [],
                "recurringLocationKeys": [],
                "recurringPropKeys": [],
                "productionWarnings": [],
            }
        )
    return {
        "seriesBible": {
            "logline": "一起完成野餐。",
            "centralTheme": "陪伴",
            "narrativeMode": "continuous",
            "worldRules": [],
            "emotionalArc": {
                "opening": "期待",
                "development": "准备",
                "climax": "野餐",
                "resolution": "满足",
            },
            "recurringLocations": [],
            "recurringProps": [],
            "wardrobeRules": [],
            "continuityRules": [],
            "visualMotifs": [],
            "soundMotifs": [],
            "forbiddenChanges": [],
        },
        "episodes": episodes,
    }


def test_series_routes_keep_planning_and_episode_materialization_explicit() -> None:
    service, client = _client()
    created = client.post("/api/v1/story-series", json=_series_payload(), headers=WRITE_HEADERS)
    assert created.status_code == 201
    series = created.json()

    preview = client.post(
        f"/api/v1/story-series/{series['id']}/plans/preview",
        json={},
        headers=WRITE_HEADERS,
    )
    assert preview.status_code == 200
    assert preview.json()["plannedEpisodeCount"] == 2

    generated = client.post(
        f"/api/v1/story-series/{series['id']}/plans/generations",
        json={
            "expectedInputHash": preview.json()["inputHash"],
            "idempotencyKey": "series-http-generation",
        },
        headers=WRITE_HEADERS,
    )
    assert generated.status_code == 202
    assert generated.json()["seriesId"] == series["id"]
    assert generated.json()["projectId"] is None

    candidate = service.complete_series_plan_job(
        uuid.UUID(generated.json()["id"]), SeriesPlanDraft.model_validate(_plan_payload())
    )
    adopted = client.post(
        f"/api/v1/story-series/{series['id']}/plans/{candidate.id}/activate",
        json={
            "expectedActivePlanVersionId": None,
            "idempotencyKey": "series-http-activation",
        },
        headers=WRITE_HEADERS,
    )
    assert adopted.status_code == 200
    episodes = client.get(f"/api/v1/story-series/{series['id']}/episodes").json()
    assert len(episodes) == 2
    assert all(item["projectId"] is None for item in episodes)

    materialized = client.post(
        f"/api/v1/story-series/{series['id']}/episodes/{episodes[0]['id']}/materialize",
        json={"idempotencyKey": "series-http-materialize"},
        headers=WRITE_HEADERS,
    )
    assert materialized.status_code == 201
    assert materialized.json()["title"] == "第1集 · 准备野餐"

    frames = client.get(
        f"/api/v1/story-series/{series['id']}/episodes/{episodes[0]['id']}/continuity/frames"
    )
    assert frames.status_code == 200
    assert frames.json() == {
        "episodeId": episodes[0]["id"],
        "sourceVideoAssetId": None,
        "lastFrame": None,
        "candidates": [],
        "selectedKeyframes": [],
    }

    too_many = client.put(
        f"/api/v1/story-series/{series['id']}/episodes/{episodes[0]['id']}/continuity/keyframes",
        json={"assetIds": [str(uuid.uuid4()) for _ in range(3)]},
        headers=WRITE_HEADERS,
    )
    assert too_many.status_code == 422


def test_failed_story_import_reanalysis_reuses_the_source_document() -> None:
    service, client = _client()
    raw_text = "主题：雨夜\n孩子和猫咪在窗边听雨。"
    preview = client.post(
        "/api/v1/story-imports/preview",
        json={"rawText": raw_text, "sourceFormat": "paste"},
        headers=WRITE_HEADERS,
    )
    assert preview.status_code == 200
    created = client.post(
        "/api/v1/story-imports",
        json={
            "rawText": raw_text,
            "sourceFormat": "paste",
            "expectedInputHash": preview.json()["inputHash"],
            "idempotencyKey": "story-import-first-attempt",
        },
        headers=WRITE_HEADERS,
    )
    assert created.status_code == 202
    document_id = created.json()["document"]["id"]
    original_job_id = created.json()["analysisJob"]["id"]
    service.cancel_job(uuid.UUID(original_job_id))

    failed_document = client.get(f"/api/v1/story-imports/{document_id}")
    assert failed_document.status_code == 200
    assert failed_document.json()["status"] == "failed"

    retried = client.post(
        f"/api/v1/story-imports/{document_id}/reanalyze",
        json={
            "expectedInputHash": preview.json()["inputHash"],
            "idempotencyKey": "story-import-retry-attempt",
        },
        headers=WRITE_HEADERS,
    )
    assert retried.status_code == 202
    assert retried.json()["id"] != original_job_id
    assert retried.json()["storySourceDocumentId"] == document_id
    assert len(client.get("/api/v1/story-imports").json()) == 1
