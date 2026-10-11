"""Inspect an encoded MP4 using ffprobe; reject unexpected video properties."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .storyboard import FORMATS


def verify_mp4(path: Path, format_name: str, duration_seconds: int) -> dict[str, object]:
    if format_name not in FORMATS or not 1 <= duration_seconds <= 30:
        raise ValueError("Invalid video specification")
    if not path.is_file() or not path.stat().st_size:
        raise ValueError("Missing or empty MP4")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height,codec_type",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=20,
    )
    metadata = json.loads(result.stdout)
    video = [s for s in metadata.get("streams", []) if s.get("codec_type") == "video"]
    if len(video) != 1:
        raise ValueError("Expected exactly one video stream")
    stream = video[0]
    width, height = FORMATS[format_name]
    if (stream.get("codec_name"), stream.get("width"), stream.get("height")) != ("h264", width, height):
        raise ValueError("Wrong video codec or dimensions")
    duration = float(metadata["format"]["duration"])
    if abs(duration - duration_seconds) > 0.2:
        raise ValueError("Unexpected video duration")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=60)
    return {"codec": "h264", "width": width, "height": height, "duration": duration}
