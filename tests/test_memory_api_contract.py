from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_panel_api_initializes_storage_before_reading():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    marker = '@app.route("/api/memory/manage", methods=["GET"])'
    start = source.index(marker)
    section = source[start : source.index("\n\n@app.route", start + len(marker))]
    assert "init_global_memory()" in section
    assert section.index("init_global_memory()") < section.index("conn = get_conn()")


def test_memory_panel_api_returns_config_and_facts(tmp_path, monkeypatch):
    import db
    from app import app
    from memory_extractor import init_global_memory

    monkeypatch.delenv("ALICE_DB_BACKEND", raising=False)
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "memory.db"))
    db.init_db()
    init_global_memory()
    conn = db.get_conn()
    conn.executemany(
        "INSERT INTO global_memory (category, fact, updated_at) VALUES (?, ?, ?)",
        [("profile", "Живёт в Москве", 1), ("profile", "Любит чай", 2)],
    )
    conn.commit()
    conn.close()

    response = app.test_client().get("/api/memory/manage")

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data["config"], dict)
    assert [fact["fact"] for fact in data["facts"]] == ["Любит чай", "Живёт в Москве"]
