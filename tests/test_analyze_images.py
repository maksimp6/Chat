"""Regression tests for offline image analysis and output safety."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError

SCRIPT = Path(__file__).with_name("analyze_images.py")
spec = importlib.util.spec_from_file_location("analyze_images", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_analyze_creates_visualizations_and_correct_stats(tmp_path):
    source = tmp_path / "sample.png"
    Image.new("RGB", (32, 24), (100, 120, 140)).save(source)
    out = tmp_path / "out"
    out.mkdir()

    result = module.analyze(source, out)

    assert result["original_size"] == [32, 24]
    assert result["oriented_size"] == [32, 24]
    assert result["rgb_mean"] == [100.0, 120.0, 140.0]
    assert result["outputs"] == {
        "edges": "sample_edges.png",
        "contrast": "sample_contrast.png",
        "equalized": "sample_equalized.png",
        "edge_heatmap": "sample_edge_heatmap.png",
    }
    for output in result["outputs"].values():
        assert (out / output).is_file()


def test_corrupt_image_fails_without_creating_diagnostic_outputs(tmp_path):
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(UnidentifiedImageError):
        module.analyze(source, out)

    assert list(out.iterdir()) == []


def test_oversized_image_is_rejected_before_decode_or_output(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (4, 3), "white").save(source)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(module, "MAX_PIXELS", 11)

    with pytest.raises(ValueError, match="11-pixel limit"):
        module.analyze(source, out)

    assert list(out.iterdir()) == []


def test_exif_orientation_is_applied_and_reported(tmp_path):
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (4, 2), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    out = tmp_path / "out"
    out.mkdir()

    result = module.analyze(source, out)

    assert result["original_size"] == [4, 2]
    assert result["oriented_size"] == [2, 4]
    assert result["orientation_exif"] == 6
    assert Image.open(out / result["outputs"]["contrast"]).size == (2, 4)


def test_analyze_refuses_to_overwrite_existing_output(tmp_path):
    source = tmp_path / "sample.png"
    Image.new("RGB", (4, 4), "white").save(source)
    out = tmp_path / "out"
    out.mkdir()
    prior = out / "sample_edges.png"
    prior.write_bytes(b"keep me")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        module.analyze(source, out)

    assert prior.read_bytes() == b"keep me"
    assert list(out.iterdir()) == [prior]


def test_png_writer_preserves_file_created_after_preflight(tmp_path):
    output = tmp_path / "race.png"
    output.write_bytes(b"owned by another process")

    with pytest.raises(FileExistsError):
        module._save_png(Image.new("RGB", (1, 1), "white"), output)

    assert output.read_bytes() == b"owned by another process"


def test_comparison_rejects_different_aspect_ratios_without_output(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (4, 2), "white").save(first)
    Image.new("RGB", (3, 2), "white").save(second)

    with pytest.raises(ValueError, match="different aspect ratios"):
        module.compare(first, second, tmp_path)

    assert not (tmp_path / "difference.png").exists()


def test_comparison_reports_dimensions_and_true_difference(tmp_path):
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    Image.new("RGB", (20, 10), "white").save(first)
    Image.new("RGB", (40, 20), "black").save(second)

    result = module.compare(first, second, tmp_path)

    assert result["files"] == ["a.png", "b.png"]
    assert result["oriented_sizes"] == [[20, 10], [40, 20]]
    assert result["comparison_size"] == [20, 10]
    assert result["mean_absolute_rgb_difference"] == [255.0, 255.0, 255.0]
    assert "not geometrically registered" in result["warning"]
    with Image.open(tmp_path / result["output"]) as output:
        assert output.size == (20, 10)


def test_cli_writes_valid_report_and_refuses_report_collision(tmp_path, monkeypatch):
    source = tmp_path / "sample.png"
    Image.new("RGB", (4, 4), "white").save(source)
    out = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv", ["analyze_images.py", str(source), "--output", str(out)]
    )
    module.main()

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["images"][0]["file"] == source.name
    assert report["images"][0]["outputs"]["edges"] == "sample_edges.png"

    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert (out / "report.json").is_file()
