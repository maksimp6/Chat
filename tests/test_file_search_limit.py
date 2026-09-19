from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_file_search_no_local_50_item_cap():
    source = (ROOT / "static" / "settings" / "settings_modal.js").read_text(encoding="utf-8")
    start = source.find("set-fs-max")
    assert start >= 0
    segment = source[start:start + 220]
    assert 'max="50"' not in segment
    assert "Math.min(" not in segment
    assert "Math.max(" in source[source.find("max_results:"):source.find("max_results:") + 300]
    assert "|| 20" in source
