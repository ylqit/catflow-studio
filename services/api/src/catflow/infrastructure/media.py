from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class InvalidMediaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMedia:
    storage_key: str
    sha256: str
    byte_size: int
    media_type: str
    content_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


_IMAGE_FORMATS = {
    "PNG": (".png", "image/png"),
    "JPEG": (".jpg", "image/jpeg"),
    "WEBP": (".webp", "image/webp"),
}


class LocalMediaStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_upload(
        self,
        payload: bytes,
        *,
        filename: str,
        declared_content_type: str,
        role: str,
    ) -> StoredMedia:
        if not payload:
            raise InvalidMediaError("media payload is empty")
        try:
            with Image.open(BytesIO(payload)) as image:
                image.load()
                image_format = str(image.format or "").upper()
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise InvalidMediaError("media decode failed") from exc
        expected = _IMAGE_FORMATS.get(image_format)
        if expected is None:
            raise InvalidMediaError(f"unsupported decoded image format: {image_format}")
        expected_extension, expected_content_type = expected
        actual_extension = Path(filename).suffix.lower()
        if image_format == "JPEG" and actual_extension == ".jpeg":
            actual_extension = ".jpg"
        if actual_extension != expected_extension:
            raise InvalidMediaError("filename extension does not match decoded media")
        if declared_content_type.lower() != expected_content_type:
            raise InvalidMediaError("declared MIME type does not match decoded media")
        digest = hashlib.sha256(payload).hexdigest()
        safe_role = "".join(
            character for character in role if character.isalnum() or character in "_-"
        )
        if not safe_role:
            raise InvalidMediaError("media role is invalid")
        storage_key = f"uploads/{safe_role}/{digest[:2]}/{digest}{expected_extension}"
        destination = self.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".partial")
            temporary.write_bytes(payload)
            temporary.replace(destination)
        return StoredMedia(
            storage_key=storage_key,
            sha256=digest,
            byte_size=len(payload),
            media_type="image",
            content_type=expected_content_type,
            width=width,
            height=height,
        )

    def resolve(self, storage_key: str) -> Path:
        if not storage_key or Path(storage_key).is_absolute():
            raise InvalidMediaError("storage key must stay inside managed media root")
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise InvalidMediaError("storage key must stay inside managed media root")
        return candidate
