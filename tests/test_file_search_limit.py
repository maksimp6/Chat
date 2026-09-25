from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_file_search_no_local_50_item_cap():
    source = (ROOT / "static" / "settings" / "settings_modal.js").read_text(encoding="utf-8")
    start = source.find("tools-fs-max")
    assert start >= 0
    segment = source[start:start + 220]
    assert 'max="50"' not in segment
    assert "Math.min(" not in segment
    max_results_start = source.find("max_results:")
    assert max_results_start >= 0
    assert "Math.max(" in source[max_results_start:max_results_start + 300]
    assert "|| 20" in source[max_results_start:max_results_start + 300]
