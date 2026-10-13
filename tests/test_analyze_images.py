"""Focused synthetic tests for the offline image analyzer."""
from pathlib import Path
import importlib.util

from PIL import Image
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_images.py"
spec = importlib.util.spec_from_file_location("analyze_images", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_analyze_creates_visualizations(tmp_path):
    source = tmp_path / "sample.png"
    Image.new("RGB", (32, 24), (100, 120, 140)).save(source)
    out = tmp_path / "out"
    out.mkdir()
    result = module.analyze(source, out)
    assert result["original_size"] == [32, 24]
    assert result["rgb_mean"] == [100.0, 120.0, 140.0]
    assert (out / "sample_edges.png").is_file()
    assert (out / "sample_contrast.png").is_file()
    assert (out / "sample_equalized.png").is_file()
    assert (out / "sample_edge_heatmap.png").is_file()


def test_difference_identical_images(tmp_path):
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    Image.new("RGB", (20, 20), "white").save(first)
    Image.new("RGB", (20, 20), "white").save(second)
    result = module.compare(first, second, tmp_path)
    assert result["mean_absolute_rgb_difference"] == [0.0, 0.0, 0.0]
    assert (tmp_path / "difference.png").exists()
