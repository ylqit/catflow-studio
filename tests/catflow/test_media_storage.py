from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from catflow.infrastructure.media import InvalidMediaError, LocalMediaStore


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 48), (220, 205, 185)).save(output, format="PNG")
    return output.getvalue()


def test_image_upload_is_decoded_hashed_and_stored_under_managed_root(tmp_path) -> None:
    store = LocalMediaStore(tmp_path)
    payload = _png()

    stored = store.save_upload(
        payload,
        filename="child.png",
        declared_content_type="image/png",
        role="episode_child",
    )

    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.media_type == "image"
    assert stored.width == 32
    assert stored.height == 48
    assert store.resolve(stored.storage_key).read_bytes() == payload
    assert store.resolve(stored.storage_key).is_relative_to(tmp_path.resolve())


def test_upload_rejects_extension_mime_and_file_header_disagreement(tmp_path) -> None:
    store = LocalMediaStore(tmp_path)

    with pytest.raises(InvalidMediaError, match="decode"):
        store.save_upload(
            b"not an image",
            filename="fake.png",
            declared_content_type="image/png",
            role="episode_child",
        )

    with pytest.raises(InvalidMediaError, match="extension"):
        store.save_upload(
            _png(),
            filename="fake.jpg",
            declared_content_type="image/png",
            role="episode_child",
        )


def test_storage_keys_cannot_escape_media_root(tmp_path) -> None:
    store = LocalMediaStore(tmp_path)

    with pytest.raises(InvalidMediaError, match="managed media root"):
        store.resolve("../../secret.txt")
