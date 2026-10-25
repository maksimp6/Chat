"""Timestamped Android capture metadata, not hardware VSync."""

import hashlib
import subprocess
import time
from pathlib import Path


def capture_frame(image, actions):
    """Capture one frame and return traceable capture interval."""
    start_mono = time.monotonic_ns()
    start_wall = time.time_ns()
    subprocess.run(
        ["python", str(actions), "screenshot"],
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    finish_mono = time.monotonic_ns()
    data = Path(image).read_bytes()
    return {
        "frame_id": hashlib.sha256(data).hexdigest()[:16],
        "capture_start_monotonic_ns": start_mono,
        "capture_end_monotonic_ns": finish_mono,
        "capture_start_unix_ns": start_wall,
        "capture_duration_ms": round((finish_mono - start_mono) / 1e6, 2),
        "display_hz": 60,
        "display_period_ms": round(1000 / 60, 3),
        "vsync_source": "unavailable",
        "vsync_aligned": False,
        "image_bytes": len(data),
    }
