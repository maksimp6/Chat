"""The profiler inspects the native Kristall journal, never the retired logos_pro.db."""

from __future__ import annotations

from storage import db
from tool_providers import profiler


def test_profiler_reports_configured_native_journal_not_working_directory_sqlite(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    (workdir / "logos_pro.db").write_bytes(b"retired sqlite" * 50)

    journal = tmp_path / "data" / "logos.memory"
    journal.parent.mkdir()
    journal.write_bytes(b"x" * 37)
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(journal))

    database = profiler.inspect_app_internals({})["database"]

    assert database == {"path": str(journal.resolve()), "exists": True, "size_bytes": 37}


def test_profiler_reports_missing_journal_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = tmp_path / "data" / "logos.memory"
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(journal))

    database = profiler.inspect_app_internals({})["database"]

    assert database == {"path": str(journal.resolve()), "exists": False, "size_bytes": 0}
    assert not journal.exists()


def test_profiler_ignores_legacy_sqlite_when_no_journal_is_configured(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "logos_pro.db").write_bytes(b"legacy")
    monkeypatch.delenv("LOGOS_MEMORY_PATH", raising=False)
    monkeypatch.setattr(db, "DB_PATH", "")

    database = profiler.inspect_app_internals({})["database"]

    assert database["path"] is None
    assert database["exists"] is False
    assert database["size_bytes"] == 0
    assert "memory path is not configured" in database["error"]
    assert (data / "logos_pro.db").read_bytes() == b"legacy"
    assert not (data / "logos.memory").exists()
