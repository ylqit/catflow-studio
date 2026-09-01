"""图片和视频技术QC。

通过的视频保持供应商原MP4直通；本模块只检查，不执行无条件重编码。
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from ...application.ports import StoredAsset


class MediaQcError(RuntimeError):
    """媒体不可读取或不满足交付技术要求。"""


class FrameExtractionError(RuntimeError):
    """FFmpeg无法稳定抽取指定数量的诊断帧。"""


class FfmpegFrameExtractor:
    """抽取首尾、近似均匀帧和显著变化帧用于只读诊断。

    本类服务最终视频语义诊断与用户选区的精确边界帧提取，不承担视频拼接或转码。
    临时帧由调用方在审核或边界资产落盘后删除。
    """

    def __init__(self, *, ffmpeg_path: Path, work_root: Path) -> None:
        self._ffmpeg_path = ffmpeg_path.expanduser().resolve()
        self._work_root = work_root.expanduser().resolve()

    def extract_review_frames(
        self,
        source: StoredAsset,
        *,
        count: int,
    ) -> tuple[Path, ...]:
        if not 4 <= count <= 12:
            raise ValueError("视频语义诊断抽帧数量必须在4至12之间")
        # 视频落盘元数据把完整技术检查保存在 qc 下；历史资产可能仍把时长放在顶层。
        # 两种布局都读取，避免已通过技术QC的成片在语义诊断阶段被误判为缺少时长。
        qc_metadata = source.metadata.get("qc")
        duration_ms = (
            qc_metadata.get("durationMs")
            if isinstance(qc_metadata, dict)
            else source.metadata.get("durationMs")
        )
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise ValueError("视频资产缺少可用于均匀抽帧的durationMs")
        self._work_root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        pattern = self._work_root / f".review-{token}-%02d.png"
        sample_count = min(30, max(count * 2, count))
        frame_rate = sample_count / (duration_ms / 1000)
        source_path = source.require_path()
        try:
            _run_ffmpeg(
                self._ffmpeg_path,
                [
                    "-i",
                    str(source_path),
                    "-vf",
                    f"fps={frame_rate:.8f}",
                    "-frames:v",
                    str(sample_count),
                    str(pattern),
                ],
            )
            candidates = tuple(sorted(self._work_root.glob(f".review-{token}-*.png")))
            if len(candidates) < count:
                raise FrameExtractionError(f"期望至少抽取{count}帧，实际得到{len(candidates)}帧")
            uniform_count = max(2, count - 2)
            selected = {
                round(index * (len(candidates) - 1) / (uniform_count - 1))
                for index in range(uniform_count)
            }
            scored_changes: list[tuple[float, int]] = []
            previous = _small_rgb(candidates[0])
            for index, path in enumerate(candidates[1:], 1):
                current = _small_rgb(path)
                score = sum(ImageStat.Stat(ImageChops.difference(previous, current)).mean)
                scored_changes.append((score, index))
                previous = current
            for _score, index in sorted(scored_changes, reverse=True):
                selected.add(index)
                if len(selected) == count:
                    break
            frames = tuple(candidates[index] for index in sorted(selected))
            for path in candidates:
                if path not in frames:
                    path.unlink(missing_ok=True)
            return frames
        except Exception:
            for frame in self._work_root.glob(f".review-{token}-*.png"):
                frame.unlink(missing_ok=True)
            raise

    def extract_frames_at(
        self,
        source: StoredAsset,
        *,
        timestamps_ms: tuple[int, ...],
    ) -> tuple[Path, ...]:
        """在精确毫秒位置抽取边界帧，供非破坏性区间编辑使用。"""

        if not timestamps_ms or len(timestamps_ms) > 12:
            raise ValueError("精确抽帧数量必须在1至12之间")
        qc_metadata = source.metadata.get("qc")
        duration_ms = (
            qc_metadata.get("durationMs")
            if isinstance(qc_metadata, dict)
            else source.metadata.get("durationMs")
        )
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise ValueError("视频资产缺少durationMs")
        if any(value < 0 or value >= duration_ms for value in timestamps_ms):
            raise ValueError("抽帧时间必须位于视频有效区间")
        self._work_root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        frames: list[Path] = []
        source_path = source.require_path()
        try:
            for index, timestamp_ms in enumerate(timestamps_ms, 1):
                output = self._work_root / f".boundary-{token}-{index}.png"
                _run_ffmpeg(
                    self._ffmpeg_path,
                    [
                        "-ss",
                        f"{timestamp_ms / 1000:.3f}",
                        "-i",
                        str(source_path),
                        "-frames:v",
                        "1",
                        str(output),
                    ],
                )
                if not output.is_file():
                    raise FrameExtractionError(f"无法抽取{timestamp_ms}ms边界帧")
                frames.append(output)
            return tuple(frames)
        except Exception:
            for frame in frames:
                frame.unlink(missing_ok=True)
            raise

    def extract_tail_frame(self, source: StoredAsset) -> tuple[Path, int]:
        """Choose the latest frame from the calmest transition near the video end."""

        qc_metadata = source.metadata.get("qc")
        duration_ms = (
            qc_metadata.get("durationMs")
            if isinstance(qc_metadata, dict)
            else source.metadata.get("durationMs")
        )
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise ValueError("视频资产缺少durationMs")
        timestamps_ms = tuple(
            dict.fromkeys(
                max(0, duration_ms - offset)
                for offset in (900, 500, 120)
                if max(0, duration_ms - offset) < duration_ms
            )
        )
        frames = self.extract_frames_at(source, timestamps_ms=timestamps_ms)
        selected_index = len(frames) - 1
        if len(frames) > 1:
            scores = []
            previous = _small_rgb(frames[0])
            for index, path in enumerate(frames[1:], 1):
                current = _small_rgb(path)
                score = sum(ImageStat.Stat(ImageChops.difference(previous, current)).mean)
                scores.append((score, -index, index))
                previous = current
            selected_index = min(scores)[2]
        for index, frame in enumerate(frames):
            if index != selected_index:
                frame.unlink(missing_ok=True)
        return frames[selected_index], timestamps_ms[selected_index]


def _small_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB").resize((64, 64)).copy()


class FfprobeMediaProbe:
    """Pillow图片检查与ffprobe视频检查。"""

    def __init__(self, ffprobe_path: Path | None) -> None:
        self._ffprobe_path = ffprobe_path

    def inspect_image(self, path: Path) -> dict[str, Any]:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                image_format = (image.format or "").lower()
                rgb = image.convert("RGB")
                edges = (
                    [rgb.getpixel((0, y)) for y in range(height)]
                    + [rgb.getpixel((width - 1, y)) for y in range(height)]
                    + [rgb.getpixel((x, 0)) for x in range(width)]
                    + [rgb.getpixel((x, height - 1)) for x in range(width)]
                )
        except (OSError, ValueError) as exc:
            raise MediaQcError(f"图片不可读取: {exc}") from exc
        if image_format not in {"png", "jpeg", "webp"}:
            raise MediaQcError(f"不支持的图片格式: {image_format}")
        dark_edge_ratio = sum(max(pixel) <= 8 for pixel in edges) / len(edges)
        return {
            "passed": True,
            "format": image_format,
            "width": width,
            "height": height,
            "ratio": width / height,
            "darkEdgeRatio": round(dark_edge_ratio, 4),
            "blackBorderDetected": dark_edge_ratio >= 0.45,
        }

    def inspect_video(
        self,
        path: Path,
        *,
        expected_duration_seconds: int,
        expected_resolution: str,
        minimum_duration_seconds: int = 4,
        maximum_duration_seconds: int = 15,
        duration_tolerance_ms: int = 1000,
        require_audio: bool = True,
    ) -> dict[str, Any]:
        payload = self._ffprobe(path)
        streams = payload.get("streams", [])
        video = next(
            (item for item in streams if item.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (item for item in streams if item.get("codec_type") == "audio"),
            None,
        )
        format_info = payload.get("format", {})
        try:
            duration_ms = round(float(format_info.get("duration")) * 1000)
        except (TypeError, ValueError):
            duration_ms = None
        width = None if video is None else video.get("width")
        height = None if video is None else video.get("height")
        expected_width = {"480p": 480, "720p": 720}.get(expected_resolution)
        failures: list[str] = []
        if video is None:
            failures.append("missing_video")
        if require_audio and audio is None:
            failures.append("missing_audio")
        if expected_width is None:
            failures.append("unsupported_expected_resolution")
        elif not isinstance(width, int) or abs(width - expected_width) > 16:
            failures.append("wrong_width")
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or height <= 0
            or abs(width / height - 9 / 16) > 0.02
        ):
            failures.append("ratio_not_9_16")
        if (
            duration_ms is None
            or not minimum_duration_seconds * 1000 <= duration_ms <= maximum_duration_seconds * 1000
            or abs(duration_ms - expected_duration_seconds * 1000) > duration_tolerance_ms
        ):
            failures.append("duration_invalid")
        return {
            "passed": not failures,
            "failures": failures,
            "container": format_info.get("format_name"),
            "videoCodec": None if video is None else video.get("codec_name"),
            "audioCodec": None if audio is None else audio.get("codec_name"),
            "frameRate": None if video is None else video.get("avg_frame_rate"),
            "timeBase": None if video is None else video.get("time_base"),
            "pixelFormat": None if video is None else video.get("pix_fmt"),
            "audioSampleRate": None if audio is None else audio.get("sample_rate"),
            "audioChannels": None if audio is None else audio.get("channels"),
            "audioChannelLayout": (None if audio is None else audio.get("channel_layout")),
            "width": width,
            "height": height,
            "durationMs": duration_ms,
            "hasAudio": audio is not None,
        }

    def _ffprobe(self, path: Path) -> dict[str, Any]:
        if self._ffprobe_path is None:
            raise MediaQcError("媒体检查需要配置ffprobe")
        try:
            completed = subprocess.run(
                [
                    str(self._ffprobe_path),
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            return json.loads(completed.stdout)
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as exc:
            raise MediaQcError(f"ffprobe检查失败: {exc}") from exc


def _run_ffmpeg(executable: Path, arguments: list[str]) -> None:
    try:
        subprocess.run(
            [str(executable), "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or "").strip()[-1000:]
        raise FrameExtractionError(f"FFmpeg抽帧失败: {detail or exc}") from exc
