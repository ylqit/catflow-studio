from __future__ import annotations

import hashlib
import os
import subprocess
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv
from PIL import Image

from catflow_worker.provider_media import ProviderMediaDownloader, UnsafeProviderUrlError


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 48), "#d8c2a8").save(buffer, format="PNG")
    return buffer.getvalue()


def test_provider_image_download_validates_and_atomically_lands_media(tmp_path: Path) -> None:
    payload = _png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "ark.cn-beijing.volces.com"
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": str(len(payload))},
            content=payload,
        )

    downloader = ProviderMediaDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolve_host=lambda _host: ("8.8.8.8",),
    )
    destination = tmp_path / "environment.png"

    landed = downloader.download_image(
        "https://ark.cn-beijing.volces.com/result.png",
        destination,
    )

    assert destination.read_bytes() == payload
    assert not destination.with_name("environment.partial.png").exists()
    assert landed.sha256 == hashlib.sha256(payload).hexdigest()
    assert landed.width == 32
    assert landed.height == 48


def test_provider_download_rejects_private_hosts_and_private_redirects(tmp_path: Path) -> None:
    downloader = ProviderMediaDownloader(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    302,
                    headers={"location": "https://127.0.0.1/stolen.png"},
                )
            )
        ),
        resolve_host=lambda _host: ("8.8.8.8",),
    )

    with pytest.raises(UnsafeProviderUrlError, match="allowed Ark media host"):
        downloader.download_image("https://127.0.0.1/result.png", tmp_path / "direct.png")

    with pytest.raises(UnsafeProviderUrlError, match="allowed Ark media host"):
        downloader.download_image(
            "https://ark.cn-beijing.volces.com/redirect.png",
            tmp_path / "redirect.png",
        )


def test_provider_download_never_keeps_a_corrupt_partial_image(tmp_path: Path) -> None:
    downloader = ProviderMediaDownloader(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=b"not-an-image",
                )
            )
        ),
        resolve_host=lambda _host: ("8.8.8.8",),
    )
    destination = tmp_path / "corrupt.png"

    with pytest.raises(ValueError, match="decode"):
        downloader.download_image(
            "https://ark.cn-beijing.volces.com/corrupt.png",
            destination,
        )

    assert not destination.exists()
    assert not destination.with_name("corrupt.partial.png").exists()


def test_provider_video_download_accepts_ark_native_480p_vertical_twelve_second_media(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    ffmpeg = Path(os.environ["FFMPEG_PATH"])
    ffprobe = Path(os.environ["FFPROBE_PATH"])
    source = tmp_path / "provider.mp4"
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xD8C2A8:s=496x864:r=24:d=12",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=60,
    )
    payload = source.read_bytes()
    downloader = ProviderMediaDownloader(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=payload)
            )
        ),
        resolve_host=lambda _host: ("8.8.8.8",),
    )
    destination = tmp_path / "landed.mp4"

    landed = downloader.download_video(
        "https://ark.cn-beijing.volces.com/result.mp4",
        destination,
        ffprobe_path=ffprobe,
        expected_duration_seconds=12,
    )

    assert landed.width == 496
    assert landed.height == 864
    assert landed.duration_ms is not None
    assert 11_500 <= landed.duration_ms <= 12_500
    assert landed.codec == "h264"
