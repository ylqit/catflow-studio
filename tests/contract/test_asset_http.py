from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from catflow.application.service import StudioService
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 36), (210, 195, 178)).save(output, format="PNG")
    return output.getvalue()


def test_uploaded_asset_is_persisted_and_served_without_exposing_disk_path(tmp_path) -> None:
    service = StudioService(MemoryStudioRepository())
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="upload-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8765",),
        ),
        media_store=LocalMediaStore(tmp_path),
    )
    client = TestClient(app)
    headers = {
        "Origin": "http://127.0.0.1:8765",
        "X-CatFlow-CSRF": "upload-csrf",
    }
    project_id = client.post(
        "/api/v1/projects",
        json={"title": "角色测试", "theme": "一致性", "targetDurationSeconds": 10},
        headers=headers,
    ).json()["id"]
    payload = _png()

    upload = client.post(
        f"/api/v1/projects/{project_id}/assets/upload?role=episode_child",
        files={"file": ("child.png", payload, "image/png")},
        headers=headers,
    )

    assert upload.status_code == 201
    asset = upload.json()
    assert "storageKey" in asset
    assert str(tmp_path) not in upload.text
    content = client.get(f"/api/v1/assets/{asset['id']}/content")
    assert content.status_code == 200
    assert content.content == payload
    assert content.headers["content-type"] == "image/png"
