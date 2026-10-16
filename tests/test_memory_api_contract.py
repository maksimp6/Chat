"""The memory management panel reads native MemoryStore, not SQL."""

from pathlib import Path

from agent_memory.client import close_memory_clients, get_memory_client

ROOT = Path(__file__).resolve().parents[1]


def test_memory_panel_api_uses_native_fact_read():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index('@app.route("/api/memory/manage", methods=["GET"])')
    section = source[start : source.index("\n\n@app.route", start + 20)]
    assert "list_global_memory_facts()" in section
    assert "get_conn()" not in section


def test_memory_panel_api_returns_config_and_sorted_facts(tmp_path, monkeypatch):
    import storage.db as db
    from app import app
    from memory_engine.extractor import save_global_memory_facts

    path = tmp_path / "facts.memory"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    save_global_memory_facts(
        [
            {"category": "profile", "fact": "Живёт в Москве"},
            {"category": "profile", "fact": "Любит чай"},
        ],
        now=1,
    )
    store = get_memory_client(path)
    # Read API order must be deterministic even for concurrent equal timestamps.
    response = app.test_client().get("/api/memory/manage")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data["config"], dict)
    assert {item["fact"] for item in data["facts"]} == {"Живёт в Москве", "Любит чай"}
    assert all(item["updated_at"] == 1 for item in data["facts"])
    assert store.get("global_memory_config") is not None
    close_memory_clients(path)


def test_fact_deduplication_and_targeted_deletion(tmp_path, monkeypatch):
    import storage.db as db
    from memory_engine.manager import clear_global_memory, load_memory_config
    from memory_engine.extractor import list_global_memory_facts, save_global_memory_facts

    path = tmp_path / "native.memory"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    assert (
        save_global_memory_facts(
            [{"category": "git", "fact": "one"}, {"category": "tools", "fact": "two"}], now=1
        )
        == 2
    )
    assert save_global_memory_facts([{"category": "git", "fact": "one"}], now=2) == 0
    assert len(list_global_memory_facts()) == 2
    config = load_memory_config()
    clear_global_memory("git")
    assert [item["fact"] for item in list_global_memory_facts()] == ["two"]
    assert load_memory_config() == config
    close_memory_clients(path)
