import json
import pytest
from printing3d.video.storyboard import load_storyboard, ffmpeg_command


def test_valid_turntable(tmp_path):
    path = tmp_path / "shot.json"
    path.write_text(
        json.dumps(
            {
                "shot": "turntable",
                "format": "vertical",
                "duration_seconds": 12,
                "source_verified": True,
            }
        )
    )
    assert load_storyboard(path)["shot"] == "turntable"


@pytest.mark.parametrize("shot", ["assembly", "exploded"])
def test_unverified_assembly_rejected(tmp_path, shot):
    path = tmp_path / "shot.json"
    path.write_text(
        json.dumps(
            {"shot": shot, "format": "horizontal", "duration_seconds": 10, "source_verified": True}
        )
    )
    with pytest.raises(ValueError, match="verified assembly"):
        load_storyboard(path)


@pytest.mark.parametrize("duration", [0, 31, 1.5, True])
def test_invalid_duration(tmp_path, duration):
    path = tmp_path / "shot.json"
    path.write_text(
        json.dumps(
            {
                "shot": "turntable",
                "format": "vertical",
                "duration_seconds": duration,
                "source_verified": True,
            }
        )
    )
    with pytest.raises(ValueError, match="Duration"):
        load_storyboard(path)


def test_ffmpeg_args():
    args = ffmpeg_command("frame_%04d.png", "output.mp4", "horizontal")
    assert args[0] == "ffmpeg"
    assert "libx264" in args
    assert "1280:720" in args[args.index("-vf") + 1]
