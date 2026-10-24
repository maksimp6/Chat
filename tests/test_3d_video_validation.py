"""Exercise real local mesh/codec fixtures and adversarial video inputs."""

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import trimesh

from printing3d.video.render import render_turntable
from printing3d.video.storyboard import ffmpeg_command, load_storyboard
from printing3d.video.verify import verify_mp4


def _plan(path: Path, **changes):
    plan = {
        "shot": "turntable",
        "format": "horizontal",
        "duration_seconds": 1,
        "source_verified": True,
    }
    plan.update(changes)
    path.write_text(json.dumps(plan))
    return path


@pytest.mark.parametrize("value", [[], None, 3, "not an object"])
def test_storyboard_requires_an_object(tmp_path, value):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="object"):
        load_storyboard(path)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("shot", [], "shot"),
        ("shot", {}, "shot"),
        ("shot", "invented", "shot"),
        ("format", [], "format"),
        ("format", {}, "format"),
        ("format", "square", "format"),
        ("source_verified", False, "verified model"),
        ("source_verified", 1, "verified model"),
    ],
)
def test_storyboard_rejects_untrusted_fields(tmp_path, key, value, message):
    path = _plan(tmp_path / "plan.json", **{key: value})
    with pytest.raises(ValueError, match=message):
        load_storyboard(path)


def test_storyboard_is_bounded_before_parsing(tmp_path):
    path = tmp_path / "plan.json"
    path.write_bytes(b" " * (16 * 1024 + 1))
    with pytest.raises(ValueError, match="16 KiB"):
        load_storyboard(path)


def test_verified_assembly_can_be_described_without_inventing_steps(tmp_path):
    path = _plan(tmp_path / "plan.json", shot="assembly", assembly_verified=True)
    assert load_storyboard(path)["shot"] == "assembly"


@pytest.mark.parametrize(("format_name", "fps"), [("square", 24), ("vertical", 25)])
def test_unsupported_encoding_specification(format_name, fps):
    with pytest.raises(ValueError, match="Unsupported"):
        ffmpeg_command("frame_%04d.png", "out.mp4", format_name, fps)


@pytest.mark.parametrize("frames", [0, 901, True, 1.5])
def test_invalid_frame_counts_never_open_a_mesh(tmp_path, frames):
    with pytest.raises(ValueError, match="frames"):
        render_turntable(tmp_path / "absent.stl", tmp_path / "frames", frames=frames)


@pytest.mark.parametrize("dpi", [49, 161, True, 90.5])
def test_invalid_resolution_never_opens_a_mesh(tmp_path, dpi):
    with pytest.raises(ValueError, match="dpi"):
        render_turntable(tmp_path / "absent.stl", tmp_path / "frames", dpi=dpi)


def test_large_stl_rejected_before_loading(tmp_path):
    source = tmp_path / "large.stl"
    with source.open("wb") as stream:
        stream.truncate(20_000_001)
    with patch("trimesh.load_mesh") as load:
        with pytest.raises(ValueError, match="20 MB"):
            render_turntable(source, tmp_path / "frames")
        load.assert_not_called()


@pytest.mark.parametrize("kind", ["scene", "empty", "open", "faces", "vertices", "nonfinite"])
def test_invalid_mesh_is_rejected_before_any_frames(tmp_path, kind):
    source = tmp_path / "source.stl"
    source.write_bytes(b"synthetic parser boundary fixture")
    mesh = trimesh.creation.box()
    if kind == "scene":
        mesh = trimesh.Scene(mesh)
    elif kind == "empty":
        mesh = trimesh.Trimesh()
    elif kind == "open":
        mesh.update_faces(np.arange(11))
    elif kind == "faces":
        mesh.faces = np.tile(mesh.faces, (8334, 1))
    elif kind == "vertices":
        mesh = trimesh.Trimesh(
            vertices=np.arange(300006, dtype=float).reshape(-1, 3),
            faces=np.arange(100002).reshape(-1, 3),
            process=False,
        )
    else:
        mesh.vertices[0, 0] = np.nan
    message = (
        "complex"
        if kind in {"faces", "vertices"}
        else "Non-finite"
        if kind == "nonfinite"
        else "watertight"
    )
    with patch("trimesh.load_mesh", return_value=mesh):
        with pytest.raises(ValueError, match=message):
            render_turntable(source, tmp_path / "frames")
    assert not (tmp_path / "frames").exists()


@pytest.mark.parametrize(
    ("format_name", "duration"),
    [("square", 1), ("vertical", 0), ("vertical", 31), ("vertical", True)],
)
def test_invalid_mp4_specification(tmp_path, format_name, duration):
    with pytest.raises(ValueError, match="specification"):
        verify_mp4(tmp_path / "absent.mp4", format_name, duration)


@pytest.mark.parametrize("exists", [False, True])
def test_missing_or_empty_mp4(tmp_path, exists):
    path = tmp_path / "video.mp4"
    if exists:
        path.touch()
    with pytest.raises(ValueError, match="Missing or empty"):
        verify_mp4(path, "vertical", 1)


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_video_stream_is_required(tmp_path, count):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"synthetic metadata fixture")
    data = {"streams": [{"codec_type": "video"}] * count}
    with patch(
        "printing3d.video.verify.subprocess.run",
        return_value=SimpleNamespace(stdout=json.dumps(data)),
    ):
        with pytest.raises(ValueError, match="exactly one"):
            verify_mp4(path, "vertical", 1)


@pytest.mark.parametrize("duration", ["0", "2", "nan", "inf", "-inf"])
def test_wrong_or_nonfinite_mp4_duration_is_rejected(tmp_path, duration):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"synthetic metadata fixture")
    data = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280}],
        "format": {"duration": duration},
    }
    with patch(
        "printing3d.video.verify.subprocess.run",
        return_value=SimpleNamespace(stdout=json.dumps(data)),
    ):
        with pytest.raises(ValueError, match="duration"):
            verify_mp4(path, "vertical", 1)


def test_cli_rejects_an_unimplemented_verified_assembly(tmp_path, monkeypatch):
    plan = _plan(tmp_path / "plan.json", shot="assembly", assembly_verified=True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "video",
            "--storyboard",
            str(plan),
            "--stl",
            str(tmp_path / "source.stl"),
            "--output",
            str(tmp_path / "output.mp4"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_module("printing3d.video", run_name="__main__")
    assert error.value.code == 2
    assert not (tmp_path / "output.mp4").exists()


def test_real_turntable_cli_encodes_decodable_h264_from_an_immutable_mesh(tmp_path, monkeypatch):
    source = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(10, 12, 8)).export(source)
    original = source.read_bytes()
    plan = _plan(tmp_path / "plan.json")
    output = tmp_path / "output.mp4"
    monkeypatch.setattr(
        sys,
        "argv",
        ["video", "--storyboard", str(plan), "--stl", str(source), "--output", str(output)],
    )
    runpy.run_module("printing3d.video", run_name="__main__")
    result = verify_mp4(output, "horizontal", 1)
    assert (result["codec"], result["width"], result["height"]) == ("h264", 1280, 720)
    assert abs(result["duration"] - 1) < 0.05
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("frame_*.png"))
    # Decode independently too, so the fixture is an actual shareable artifact.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
        check=True,
        timeout=15,
    )
