from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from catflow.application.service import PlannerMessageCommand, StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app

WRITE_HEADERS = {
    "Origin": "http://127.0.0.1:8877",
    "X-CatFlow-CSRF": "library-csrf",
}


def _client() -> tuple[StudioService, TestClient]:
    service = StudioService(MemoryStudioRepository())
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="library-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
        ),
    )
    return service, TestClient(app)


def _create_project(client: TestClient, title: str, theme: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/projects",
        json={"title": title, "theme": theme, "targetDurationSeconds": 12},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 201
    return response.json()


def test_project_library_pages_without_replacing_the_legacy_project_list() -> None:
    _, client = _client()
    for index in range(14):
        _create_project(client, f"雨天短片 {index:02d}", "雨天")

    first = client.get("/api/v1/project-library?limit=12")
    assert first.status_code == 200
    document = first.json()
    assert len(document["items"]) == 12
    assert document["total"] == 14
    assert document["nextCursor"]
    assert all(item["stage"] == "story" for item in document["items"])
    assert all(
        item["tags"] == [{"name": "雨天", "normalizedName": "雨天"}] for item in document["items"]
    )

    second = client.get(
        "/api/v1/project-library",
        params={"limit": 12, "cursor": document["nextCursor"]},
    ).json()
    assert len(second["items"]) == 2
    assert {item["id"] for item in document["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )
    assert len(client.get("/api/v1/projects").json()) == 14


def test_collections_tags_search_and_batch_actions_are_product_behaviors() -> None:
    _, client = _client()
    rain = _create_project(client, "雨天擦爪", "孩子给猫咪擦干湿爪")
    window = _create_project(client, "窗边纸星星", "午后窗边折纸")

    collection = client.post(
        "/api/v1/project-collections",
        json={"name": "居家日常", "colorKey": "sage"},
        headers=WRITE_HEADERS,
    )
    assert collection.status_code == 201
    collection_id = collection.json()["id"]

    organized = client.patch(
        f"/api/v1/projects/{rain['id']}/organization",
        json={
            "collectionId": collection_id,
            "tags": ["雨天", " 室内 ", "雨天"],
            "pinned": True,
        },
        headers=WRITE_HEADERS,
    )
    assert organized.status_code == 200
    assert organized.json()["collection"]["name"] == "居家日常"
    assert [tag["name"] for tag in organized.json()["tags"]] == ["雨天", "室内"]
    assert organized.json()["pinned"] is True

    searched = client.get("/api/v1/project-library", params={"q": "湿爪"}).json()
    assert [item["id"] for item in searched["items"]] == [rain["id"]]
    filtered = client.get(
        "/api/v1/project-library",
        params=[("tags", "雨天"), ("tags", "室内")],
    ).json()
    assert [item["id"] for item in filtered["items"]] == [rain["id"]]

    batch = client.post(
        "/api/v1/project-library/actions",
        json={"action": "add_tags", "projectIds": [rain["id"], window["id"]], "tags": ["暖光"]},
        headers=WRITE_HEADERS,
    )
    assert batch.status_code == 200
    assert batch.json()["updatedCount"] == 2
    warm = client.get("/api/v1/project-library", params={"tags": "暖光"}).json()
    assert {item["id"] for item in warm["items"]} == {rain["id"], window["id"]}

    unassigned = client.get("/api/v1/project-library", params={"unassigned": True}).json()
    assert {item["id"] for item in unassigned["items"]} == {window["id"]}


def test_archiving_is_atomic_and_rejects_a_project_with_a_running_job() -> None:
    service, client = _client()
    running = _create_project(client, "正在规划", "安静日常")
    idle = _create_project(client, "可以归档", "旧灵感")
    running_id = uuid.UUID(str(running["id"]))
    snapshot = service.get_planner(running_id)
    service.enqueue_planner_message(
        running_id,
        PlannerMessageCommand(
            text="继续规划",
            expectedContextRevision=snapshot.context_revision,
            idempotencyKey="library-running-job",
        ),
    )

    response = client.post(
        "/api/v1/project-library/actions",
        json={"action": "archive", "projectIds": [running["id"], idle["id"]]},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 409

    active = client.get("/api/v1/project-library").json()
    assert {item["id"] for item in active["items"]} == {running["id"], idle["id"]}
    archived = client.get("/api/v1/project-library", params={"systemView": "archived"}).json()
    assert archived["items"] == []


def test_project_cover_prefers_the_selected_final_video_poster() -> None:
    service, client = _client()
    project = _create_project(client, "成片封面", "整理毛巾")
    project_id = uuid.UUID(str(project["id"]))
    selected_video = service.register_asset(
        project_id, role="video", media_type="video", sha256="1" * 64
    )
    selected_final = service.register_asset(
        project_id, role="final", media_type="video", sha256="2" * 64
    )
    service.register_asset(
        project_id,
        role="project_poster",
        media_type="image",
        sha256="3" * 64,
        metadata={"sourceAssetId": str(selected_video.id)},
    )
    final_poster = service.register_asset(
        project_id,
        role="project_poster",
        media_type="image",
        sha256="4" * 64,
        metadata={"sourceAssetId": str(selected_final.id)},
    )
    service.select_asset(project_id, slot="video", asset_id=selected_video.id)
    service.select_asset(project_id, slot="final", asset_id=selected_final.id)

    item = client.get("/api/v1/project-library").json()["items"][0]
    assert item["coverAssetId"] == str(final_poster.id)


def test_collection_and_batch_tag_validation_are_safe_and_atomic() -> None:
    _, client = _client()
    first = _create_project(client, "第一条", "简短主题")
    second = _create_project(client, "第二条", "另一个主题")
    invalid_collection = client.post(
        "/api/v1/project-collections",
        json={"name": "   ", "colorKey": "clay"},
        headers=WRITE_HEADERS,
    )
    assert invalid_collection.status_code == 422

    eight_tags = [f"标签{index}" for index in range(8)]
    organized = client.patch(
        f"/api/v1/projects/{second['id']}/organization",
        json={"tags": eight_tags},
        headers=WRITE_HEADERS,
    )
    assert organized.status_code == 200
    failed_batch = client.post(
        "/api/v1/project-library/actions",
        json={
            "action": "add_tags",
            "projectIds": [first["id"], second["id"]],
            "tags": ["额外标签"],
        },
        headers=WRITE_HEADERS,
    )
    assert failed_batch.status_code == 422

    first_after = client.get("/api/v1/project-library", params={"q": "第一条"}).json()["items"][0]
    assert [tag["name"] for tag in first_after["tags"]] == ["简短主题"]
    suggestions = client.get("/api/v1/project-tags", params={"query": "标签"}).json()
    assert {item["name"] for item in suggestions} == set(eight_tags)
