import pytest
import trimesh
from printing3d.video.render import render_turntable


def test_reject_missing_stl(tmp_path):
    with pytest.raises(ValueError, match="Expected existing STL"):
        render_turntable(tmp_path / "missing.stl", tmp_path / "frames")


def test_reject_invalid_frame_count(tmp_path):
    with pytest.raises(ValueError, match="frames"):
        render_turntable(tmp_path / "missing.stl", tmp_path / "frames", frames=901)


def test_turntable_cube(tmp_path):
    mesh = trimesh.creation.box(extents=(10, 12, 8))
    stl = tmp_path / "cube.stl"
    mesh.export(stl)
    frames = render_turntable(stl, tmp_path / "frames", frames=3, dpi=50)
    assert len(frames) == 3
    assert all(frame.stat().st_size > 1000 for frame in frames)
