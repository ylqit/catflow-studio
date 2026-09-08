from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .media_probe import inspect_video


class UnsafeProviderUrlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LandedProviderMedia:
    sha256: str
    byte_size: int
    width: int
    height: int
    duration_ms: int | None = None
    codec: str | None = None
    media_facts: dict[str, object] = field(default_factory=dict)


class ProviderMediaDownloader:
    IMAGE_LIMIT_BYTES = 30 * 1024 * 1024
    VIDEO_LIMIT_BYTES = 512 * 1024 * 1024
    MAX_REDIRECTS = 4
    ALLOWED_HOST_SUFFIXES = ("volces.com", "volcengineapi.com")

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        resolve_host: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=httpx.Timeout(120))
        self._resolve_host = resolve_host or _resolve_host

    def download_image(self, url: str, destination: Path) -> LandedProviderMedia:
        partial = destination.with_name(f"{destination.stem}.partial{destination.suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        try:
            self._download(url, partial, maximum_bytes=self.IMAGE_LIMIT_BYTES)
            try:
                with Image.open(partial) as image:
                    image.verify()
                with Image.open(partial) as image:
                    width, height = image.size
            except (OSError, UnidentifiedImageError) as exc:
                raise ValueError("provider image failed decode validation") from exc
            digest = _sha256(partial)
            byte_size = partial.stat().st_size
            partial.replace(destination)
            return LandedProviderMedia(
                sha256=digest,
                byte_size=byte_size,
                width=width,
                height=height,
            )
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def download_video(
        self,
        url: str,
        destination: Path,
        *,
        ffprobe_path: Path,
        expected_duration_seconds: int,
    ) -> LandedProviderMedia:
        partial = destination.with_name(f"{destination.stem}.partial{destination.suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        try:
            self._download(url, partial, maximum_bytes=self.VIDEO_LIMIT_BYTES)
            metadata = inspect_video(partial, ffprobe_path)
            width = int(metadata["width"])
            height = int(metadata["height"])
            duration_ms = int(metadata["durationMs"])
            codec = str(metadata["codec"])
            if not (
                480 <= width <= 512
                and 840 <= height <= 896
                and abs(width / height - 9 / 16) <= 0.02
            ):
                raise ValueError(f"provider video is not 480p 9:16: {width}x{height}")
            expected_ms = expected_duration_seconds * 1000
            if not expected_ms - 500 <= duration_ms <= expected_ms + 500:
                raise ValueError(f"provider video duration is outside tolerance: {duration_ms} ms")
            if codec not in {"h264", "hevc"}:
                raise ValueError(f"unsupported provider video codec: {codec}")
            digest = _sha256(partial)
            byte_size = partial.stat().st_size
            partial.replace(destination)
            return LandedProviderMedia(
                sha256=digest,
                byte_size=byte_size,
                width=width,
                height=height,
                duration_ms=duration_ms,
                codec=codec,
                media_facts=metadata,
            )
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _download(self, url: str, destination: Path, *, maximum_bytes: int) -> None:
        current = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_url(current)
            with self._client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("provider redirect omitted its location")
                    if redirect_count >= self.MAX_REDIRECTS:
                        raise ValueError("provider media exceeded redirect limit")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > maximum_bytes:
                    raise ValueError("provider media exceeds byte limit")
                total = 0
                with destination.open("wb") as stream:
                    for block in response.iter_bytes():
                        total += len(block)
                        if total > maximum_bytes:
                            raise ValueError("provider media exceeds byte limit")
                        stream.write(block)
                return
        raise ValueError("provider media redirect loop")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise UnsafeProviderUrlError("provider URL must use HTTPS")
        host = parsed.hostname.rstrip(".").lower()
        if not any(
            host == suffix or host.endswith(f".{suffix}") for suffix in self.ALLOWED_HOST_SUFFIXES
        ):
            raise UnsafeProviderUrlError("provider URL is not an allowed Ark media host")
        addresses = self._resolve_host(host)
        if not addresses:
            raise UnsafeProviderUrlError("provider media host did not resolve")
        for value in addresses:
            address = ipaddress.ip_address(value)
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise UnsafeProviderUrlError("provider media host resolved to a private address")


def _resolve_host(host: str) -> tuple[str, ...]:
    return tuple(
        sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
