"""Local media transport for ``cvg-fake://`` provider results."""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from ...application.ports import LandedAsset
from ..media.storage import AssetStorageError, LocalAssetStore


class FakeProviderAssetStore(LocalAssetStore):
    """Generate deterministic local fixtures, delegate ordinary URLs unchanged."""

    def __init__(
        self,
        *,
        work_root: Path,
        asset_root: Path,
        ffmpeg_path: Path | None,
        max_bytes: int = 2_000_000_000,
    ) -> None:
        super().__init__(
            work_root=work_root,
            asset_root=asset_root,
            ffmpeg_path=ffmpeg_path,
            max_bytes=max_bytes,
        )
        self._fake_work_root = work_root.expanduser().resolve()
        self._fake_ffmpeg = None if ffmpeg_path is None else ffmpeg_path.expanduser().resolve()

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        parsed = urlparse(url)
        if parsed.scheme != "cvg-fake":
            return super().download(url, suffix=suffix)
        self._fake_work_root.mkdir(parents=True, exist_ok=True)
        if parsed.netloc == "image":
            temporary = self._fake_work_root / f".fake-{uuid.uuid4().hex}.png"
            try:
                self._render_image(temporary, parsed.path)
                return self.import_local(temporary)
            finally:
                temporary.unlink(missing_ok=True)
        if parsed.netloc == "video":
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 3 or parts[1] not in {"480p", "720p"}:
                raise AssetStorageError("invalid fake provider video URL")
            try:
                duration = int(parts[0])
            except ValueError as exc:
                raise AssetStorageError("invalid fake provider video duration") from exc
            temporary = self._fake_work_root / f".fake-{uuid.uuid4().hex}.mp4"
            try:
                self._render_video(temporary, duration=duration, resolution=parts[1])
                return self.import_local(temporary)
            finally:
                temporary.unlink(missing_ok=True)
        raise AssetStorageError("unknown fake provider media type")

    @staticmethod
    def _render_image(path: Path, token: str) -> None:
        seed = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:6], 16)
        accent = (110 + seed % 70, 145 + (seed // 7) % 60, 120 + (seed // 17) % 70)
        image = Image.new("RGB", (720, 1280), (243, 238, 224))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 720, 520), fill=(222, 235, 224))
        draw.rectangle((0, 520, 720, 1280), fill=(224, 202, 170))
        draw.ellipse((180, 230, 420, 470), fill=(245, 213, 179), outline=(70, 62, 54), width=6)
        draw.polygon(((190, 270), (300, 130), (410, 270)), fill=(65, 54, 48))
        draw.ellipse((430, 650, 650, 870), fill=(205, 207, 198), outline=(70, 72, 70), width=6)
        draw.ellipse((470, 690, 525, 745), fill=(250, 247, 239))
        draw.ellipse((555, 690, 610, 745), fill=(250, 247, 239))
        draw.line((70, 940, 650, 940), fill=accent, width=18)
        draw.line((120, 1020, 600, 1020), fill=accent, width=12)
        try:
            heading_font = ImageFont.truetype("arialbd.ttf", 42)
            detail_font = ImageFont.truetype("arial.ttf", 24)
        except OSError:
            heading_font = ImageFont.load_default()
            detail_font = heading_font
        draw.rectangle((8, 8, 712, 1272), outline=(186, 92, 58), width=16)
        draw.rectangle((28, 28, 692, 150), fill=(48, 35, 30))
        draw.text(
            (54, 50),
            "FAKE / LOCAL FIXTURE",
            fill=(255, 218, 151),
            font=heading_font,
        )
        draw.text(
            (55, 108),
            "NOT MODEL OUTPUT",
            fill=(255, 181, 132),
            font=detail_font,
        )
        draw.rectangle((28, 1120, 692, 1248), fill=(48, 35, 30))
        draw.text(
            (55, 1150),
            "DATA-FLOW TEST ONLY",
            fill=(255, 218, 151),
            font=heading_font,
        )
        image.save(path, format="PNG")

    def _render_video(self, path: Path, *, duration: int, resolution: str) -> None:
        if self._fake_ffmpeg is None or not self._fake_ffmpeg.is_file():
            raise AssetStorageError("fake video generation requires ffmpeg")
        size = "480x854" if resolution == "480p" else "720x1280"
        command = [
            str(self._fake_ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#dfe9dc:s={size}:r=24:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not path.is_file():
            raise AssetStorageError(
                "fake video rendering failed: " + completed.stderr.strip()[-1_000:]
            )
