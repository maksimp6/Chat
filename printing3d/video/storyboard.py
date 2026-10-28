"""Validate 3D video manifests and generate safe FFmpeg command arguments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_SHOTS = {"turntable", "exploded", "assembly", "print_orientation"}
FORMATS = {"vertical": (720, 1280), "horizontal": (1280, 720)}


def load_storyboard(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 16 * 1024:
        raise ValueError("Storyboard exceeds 16 KiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Storyboard must be an object")
    if type(data.get("shot")) is not str or data["shot"] not in ALLOWED_SHOTS:
        raise ValueError("Unsupported shot")
    if type(data.get("format")) is not str or data["format"] not in FORMATS:
        raise ValueError("Unsupported format")
    duration = data.get("duration_seconds")
    if type(duration) is not int or not 1 <= duration <= 30:
        raise ValueError("Duration must be an integer from 1 to 30 seconds")
    if (
        data["shot"] in {"exploded", "assembly"}
        and data.get("assembly_verified") is not True
    ):
        raise ValueError("Assembly video requires verified assembly")
    if data.get("source_verified") is not True:
        raise ValueError("Video requires verified model source")
    return data


def ffmpeg_command(
    frames_pattern: str, output: str, format_name: str, fps: int = 24
) -> list[str]:
    if format_name not in FORMATS:
        raise ValueError("Unsupported format")
    if fps not in (24, 30):
        raise ValueError("Unsupported fps")
    width, height = FORMATS[format_name]
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        frames_pattern,
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output,
    ]
