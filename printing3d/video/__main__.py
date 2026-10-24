"""Offline render and encode a verified STL turntable."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from .render import render_turntable
from .storyboard import ffmpeg_command, load_storyboard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storyboard", type=Path, required=True)
    parser.add_argument("--stl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = load_storyboard(args.storyboard)
    if plan["shot"] != "turntable":
        parser.error("Only turntable rendering is implemented")
    fps = 24
    with tempfile.TemporaryDirectory(prefix="alice-3d-") as temp:
        folder = Path(temp)
        render_turntable(args.stl, folder, frames=plan["duration_seconds"] * fps)
        command = ffmpeg_command(
            str(folder / "frame_%04d.png"), str(args.output), plan["format"], fps=fps
        )
        subprocess.run(command, check=True, timeout=180)


if __name__ == "__main__":
    main()
