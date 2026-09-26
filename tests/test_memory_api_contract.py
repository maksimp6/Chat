from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_panel_api_initializes_storage_before_reading():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    marker = '@app.route("/api/memory/manage", methods=["GET"])'
    start = source.index(marker)
    section = source[start : source.index("\n\n@app.route", start + len(marker))]
    assert "init_global_memory()" in section
    assert section.index("init_global_memory()") < section.index("conn = get_conn()")
