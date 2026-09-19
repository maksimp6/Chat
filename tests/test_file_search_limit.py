from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_file_search_no_local_50_item_cap():
    source = (ROOT / "static" / "settings" / "settings_modal.js").read_text(encoding="utf-8")
    assert "Math.min(" not in source[source.find("max_results:"):source.find("max_results:")+300]
    assert "|| 20" in source
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="set-fs-max"' in template
    segment = template[template.find('id="set-fs-max"')-120:template.find('id="set-fs-max"')+160]
    assert "max="50"" not in segment
