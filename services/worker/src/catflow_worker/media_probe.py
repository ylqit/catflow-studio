from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path


def inspect_video(
    path: Path, ffprobe_path: Path, *, ffmpeg_path: Path | None = None
) -> dict[str, object]:
    """Measure decoded frames and actual audio, independent of requested provider duration."""
    result = subprocess.run(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    document = json.loads(result.stdout)
    video = next(stream for stream in document["streams"] if stream["codec_type"] == "video")
    audio = next(
        (stream for stream in document["streams"] if stream["codec_type"] == "audio"), None
    )
    rate = Fraction(video["avg_frame_rate"])
    frames = int(video["nb_read_frames"])
    if rate <= 0 or frames <= 0:
        raise ValueError("video has no measurable frames or frame rate")
    facts = {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "codec": video["codec_name"],
        "durationMs": round(float(document["format"]["duration"]) * 1000),
        "durationFrames": frames,
        "frameCount": frames,
        "frameRateNumerator": rate.numerator,
        "frameRateDenominator": rate.denominator,
        "hasAudio": audio is not None,
        "audioState": "present" if audio else "absent",
        "audioCodec": audio.get("codec_name") if audio else None,
        "audioChannels": int(audio["channels"]) if audio else 0,
        "audioSampleRate": int(audio["sample_rate"]) if audio else None,
        "mediaInspectionVersion": 1,
    }
    if audio is not None and ffmpeg_path is not None:
        measured = subprocess.run(
            [
                str(ffmpeg_path),
                "-hide_banner",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-af",
                "astats=reset=0",
                "-vn",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        peaks = re.findall(r"Peak level dB: (-?inf|[-\d.]+)", measured.stderr)
        if peaks:
            facts["audioState"] = "silent" if all(value == "-inf" for value in peaks) else "present"
    return facts
