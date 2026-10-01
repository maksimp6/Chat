"""Tests for the RAG pipeline manager and routes."""

from __future__ import annotations

import io
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import rag as _rag_mod
from rag import (
    add_document,
    attach_to_conversation,
    create_pipeline,
    delete_pipeline,
    detach_from_conversation,
    get_pipeline,
    init_rag_tables,
    list_pipelines,
    list_pipelines_for_conversation,
)



# ---------------------------------------------------------------------------
# Fixtures: in-memory SQLite for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file to avoid state bleed."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("ALICE_DB_PATH", db_path)
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("ALICE_DB_BACKEND", raising=False)

    # Re-import get_conn so it picks up the env var.
    import importlib
    import db as db_mod
    importlib.reload(db_mod)
    # Re-patch get_conn/save_conv_settings/get_conv_settings in rag module.
    monkeypatch.setattr(_rag_mod, "get_conn", db_mod.get_conn)
    monkeypatch.setattr(_rag_mod, "save_conv_settings", db_mod.save_conv_settings)
    monkeypatch.setattr(_rag_mod, "get_conv_settings", db_mod.get_conv_settings)

    db_mod.init_db()
    init_rag_tables()
    yield db_mod


@pytest.fixture()
def fake_client(monkeypatch):
    """Patch _client() to return a mock that never hits the network."""
    mock = MagicMock()
    mock.create_vector_store.return_value = {"id": "vs-test-001"}
    mock.delete_vector_store.return_value = {"deleted": True}
    mock.upload_file.return_value = {"id": "file-001", "filename": "doc.txt"}
    mock.add_file_to_vs.return_value = {"id": "vsf-001", "status": "in_progress"}
    monkeypatch.setattr(_rag_mod, "_client", lambda: mock)
    return mock


# ---------------------------------------------------------------------------
# Unit tests – pipeline CRUD
# ---------------------------------------------------------------------------

def test_create_pipeline_records_vs_id(fake_client):
    p = create_pipeline("My KB")
    assert p["id"]
    assert p["name"] == "My KB"
    assert p["vs_id"] == "vs-test-001"
    assert p["status"] == "ready"
    fake_client.create_vector_store.assert_called_once_with(name="My KB")


def test_create_pipeline_survives_vs_failure(monkeypatch):
    from yandex_client import YandexClientError

    def bad_client():
        m = MagicMock()
        m.create_vector_store.side_effect = YandexClientError("quota exceeded")
        return m

    monkeypatch.setattr(_rag_mod, "_client", bad_client)
    p = create_pipeline("Offline KB")
    assert p["status"] == "created"
    assert p["vs_id"] is None


def test_get_pipeline_returns_none_for_unknown():
    assert get_pipeline("does-not-exist") is None


def test_get_pipeline_roundtrip(fake_client):
    p = create_pipeline("Roundtrip KB")
    fetched = get_pipeline(p["id"])
    assert fetched is not None
    assert fetched["id"] == p["id"]
    assert fetched["name"] == "Roundtrip KB"


def test_list_pipelines_empty():
    assert list_pipelines() == []


def test_list_pipelines_after_create(fake_client):
    create_pipeline("A")
    create_pipeline("B")
    pipelines = list_pipelines()
    assert len(pipelines) == 2


def test_delete_pipeline_returns_false_for_unknown():
    assert delete_pipeline("ghost-id") is False


def test_delete_pipeline_removes_record(fake_client):
    p = create_pipeline("To Delete")
    assert delete_pipeline(p["id"]) is True
    assert get_pipeline(p["id"]) is None
    fake_client.delete_vector_store.assert_called_once_with("vs-test-001")


def test_delete_pipeline_tolerates_vs_error(fake_client):
    from yandex_client import YandexClientError

    fake_client.delete_vector_store.side_effect = YandexClientError("already gone")
    p = create_pipeline("Partial Delete")
    assert delete_pipeline(p["id"]) is True
    assert get_pipeline(p["id"]) is None


# ---------------------------------------------------------------------------
# Unit tests – document upload
# ---------------------------------------------------------------------------

def test_add_document_uploads_and_attaches(fake_client):
    p = create_pipeline("Doc KB")
    result = add_document(p["id"], b"hello world", "doc.txt")
    assert result["file"]["id"] == "file-001"
    assert result["vs_file"]["id"] == "vsf-001"
    fake_client.upload_file.assert_called_once()
    fake_client.add_file_to_vs.assert_called_once_with("vs-test-001", "file-001")


def test_add_document_raises_for_unknown_pipeline(fake_client):
    with pytest.raises(ValueError, match="not found"):
        add_document("ghost", b"data", "file.txt")


def test_add_document_raises_when_no_vs(monkeypatch):
    from yandex_client import YandexClientError

    def bad_client():
        m = MagicMock()
        m.create_vector_store.side_effect = YandexClientError("unavailable")
        return m

    monkeypatch.setattr(_rag_mod, "_client", bad_client)
    p = create_pipeline("No VS")
    assert p["vs_id"] is None

    # Now restore a working client for the add_document call.
    good = MagicMock()
    good.upload_file.return_value = {"id": "f-1"}
    monkeypatch.setattr(_rag_mod, "_client", lambda: good)

    with pytest.raises(ValueError, match="no vector store"):
        add_document(p["id"], b"data", "x.txt")


# ---------------------------------------------------------------------------
# Unit tests – conversation attachment
# ---------------------------------------------------------------------------

def test_attach_updates_conv_settings(fake_client, isolated_db):
    p = create_pipeline("Attach KB")
    isolated_db.create_conversation("conv-1", "Test conv", "aliceai-llm")
    attach_to_conversation(p["id"], "conv-1", max_results=5)

    settings = isolated_db.get_conv_settings("conv-1") or {}
    fs = settings.get("tools_config", {}).get("file_search", {})
    assert fs.get("enabled") is True
    assert "vs-test-001" in fs.get("vector_store_ids", "")
    assert fs.get("max_results") == 5


def test_detach_disables_file_search(fake_client, isolated_db):
    p = create_pipeline("Detach KB")
    isolated_db.create_conversation("conv-2", "Test conv 2", "aliceai-llm")
    attach_to_conversation(p["id"], "conv-2")
    detach_from_conversation(p["id"], "conv-2")

    settings = isolated_db.get_conv_settings("conv-2") or {}
    fs = settings.get("tools_config", {}).get("file_search", {})
    assert not fs.get("enabled")
    assert fs.get("vector_store_ids", "") == ""


def test_attach_raises_for_unknown_pipeline(fake_client):
    with pytest.raises(ValueError, match="not found"):
        attach_to_conversation("ghost-pipe", "conv-3")


def test_list_pipelines_for_conversation(fake_client, isolated_db):
    p1 = create_pipeline("KB 1")
    p2 = create_pipeline("KB 2")
    isolated_db.create_conversation("conv-4", "C4", "aliceai-llm")
    attach_to_conversation(p1["id"], "conv-4")
    attach_to_conversation(p2["id"], "conv-4")

    linked = list_pipelines_for_conversation("conv-4")
    ids = {l["id"] for l in linked}
    assert p1["id"] in ids
    assert p2["id"] in ids


def test_delete_pipeline_syncs_conv_settings(fake_client, isolated_db):
    p = create_pipeline("To Sync")
    isolated_db.create_conversation("conv-5", "C5", "aliceai-llm")
    attach_to_conversation(p["id"], "conv-5")
    delete_pipeline(p["id"])

    settings = isolated_db.get_conv_settings("conv-5") or {}
    fs = settings.get("tools_config", {}).get("file_search", {})
    assert not fs.get("enabled")


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client(fake_client):
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_route_list_pipelines_empty(flask_client):
    resp = flask_client.get("/api/rag/pipelines")
    assert resp.status_code == 200
    assert resp.get_json()["pipelines"] == []


def test_route_create_pipeline(flask_client):
    resp = flask_client.post("/api/rag/pipelines", json={"name": "Route KB"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "Route KB"
    assert data["id"]


def test_route_create_pipeline_missing_name(flask_client):
    resp = flask_client.post("/api/rag/pipelines", json={})
    assert resp.status_code == 400


def test_route_get_pipeline_not_found(flask_client):
    resp = flask_client.get("/api/rag/pipelines/nonexistent")
    assert resp.status_code == 404


def test_route_delete_pipeline(flask_client):
    r = flask_client.post("/api/rag/pipelines", json={"name": "Del KB"})
    pid = r.get_json()["id"]
    resp = flask_client.delete(f"/api/rag/pipelines/{pid}")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "deleted"


def test_route_add_document_no_file(flask_client):
    r = flask_client.post("/api/rag/pipelines", json={"name": "NoFile KB"})
    pid = r.get_json()["id"]
    resp = flask_client.post(f"/api/rag/pipelines/{pid}/documents")
    assert resp.status_code == 400


def test_route_add_document_bad_extension(flask_client):
    r = flask_client.post("/api/rag/pipelines", json={"name": "BadExt KB"})
    pid = r.get_json()["id"]
    data = {"file": (io.BytesIO(b"data"), "script.py")}
    resp = flask_client.post(
        f"/api/rag/pipelines/{pid}/documents",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_route_add_document_success(flask_client):
    r = flask_client.post("/api/rag/pipelines", json={"name": "Upload KB"})
    pid = r.get_json()["id"]
    data = {"file": (io.BytesIO(b"hello world"), "notes.txt")}
    resp = flask_client.post(
        f"/api/rag/pipelines/{pid}/documents",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201


def test_route_attach_and_conv_rag(flask_client, isolated_db):
    r = flask_client.post("/api/rag/pipelines", json={"name": "Conv KB"})
    pid = r.get_json()["id"]
    isolated_db.create_conversation("conv-route-1", "Title", "aliceai-llm")

    resp = flask_client.put(
        f"/api/rag/pipelines/{pid}/conversations/conv-route-1",
        json={"max_results": 10},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "attached"

    resp2 = flask_client.get("/api/conversations/conv-route-1/rag")
    assert resp2.status_code == 200
    pipelines = resp2.get_json()["pipelines"]
    assert any(p["id"] == pid for p in pipelines)


def test_route_detach(flask_client, isolated_db):
    r = flask_client.post("/api/rag/pipelines", json={"name": "Detach Route KB"})
    pid = r.get_json()["id"]
    isolated_db.create_conversation("conv-route-2", "Title", "aliceai-llm")
    flask_client.put(f"/api/rag/pipelines/{pid}/conversations/conv-route-2", json={})

    resp = flask_client.delete(f"/api/rag/pipelines/{pid}/conversations/conv-route-2")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "detached"

    resp2 = flask_client.get("/api/conversations/conv-route-2/rag")
    assert resp2.get_json()["pipelines"] == []
