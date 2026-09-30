import pytest

from app import app
from app_surfaces.registry import (
    MAX_FRAME_BYTES,
    SurfaceAuthError,
    SurfaceNotFound,
    SurfaceRegistry,
    SurfaceValidationError,
    surface_registry,
)


@pytest.fixture(autouse=True)
def reset_surface_registry():
    surface_registry.reset()
    yield
    surface_registry.reset()


def _register(client):
    response = client.post(
        "/api/app-surfaces",
        json={
            "title": "Calculator",
            "source": "wine:calc.exe",
            "width": 800,
            "height": 600,
        },
    )
    assert response.status_code == 201
    return response.get_json()


def test_surface_api_round_trip_frame_etag_input_and_delete():
    client = app.test_client()
    registered = _register(client)
    surface = registered["surface"]
    token = registered["producer_token"]
    surface_id = surface["id"]

    listing = client.get("/api/app-surfaces")
    assert listing.status_code == 200
    assert listing.get_json()["surfaces"][0]["has_frame"] is False

    frame = b"fake-jpeg-frame"
    published = client.put(
        f"/api/app-surfaces/{surface_id}/frame",
        data=frame,
        headers={
            "Content-Type": "image/jpeg",
            "X-Alice-Surface-Token": token,
        },
    )
    assert published.status_code == 200
    assert published.get_json()["surface"]["frame_version"] == 1

    fetched = client.get(f"/api/app-surfaces/{surface_id}/frame")
    assert fetched.status_code == 200
    assert fetched.data == frame
    assert fetched.mimetype == "image/jpeg"
    assert fetched.headers["Cache-Control"] == "no-store"
    assert fetched.headers["X-Alice-Surface-Version"] == "1"
    etag = fetched.headers["ETag"]

    unchanged = client.get(
        f"/api/app-surfaces/{surface_id}/frame",
        headers={"If-None-Match": etag},
    )
    assert unchanged.status_code == 304

    queued = client.post(
        f"/api/app-surfaces/{surface_id}/input",
        json={"type": "mouse_down", "x": 100, "y": 200, "button": 1},
    )
    assert queued.status_code == 202
    event = queued.get_json()["event"]
    assert event["type"] == "mouse_down"
    assert event["x"] == 100
    assert event["y"] == 200
    assert event["button"] == 1
    assert "ts" in event

    drained = client.get(
        f"/api/app-surfaces/{surface_id}/input?limit=1",
        headers={"X-Alice-Surface-Token": token},
    )
    assert drained.status_code == 200
    assert drained.get_json()["events"] == [event]

    empty = client.get(
        f"/api/app-surfaces/{surface_id}/input",
        headers={"X-Alice-Surface-Token": token},
    )
    assert empty.get_json()["events"] == []

    deleted = client.delete(
        f"/api/app-surfaces/{surface_id}",
        headers={"X-Alice-Surface-Token": token},
    )
    assert deleted.status_code == 200
    assert deleted.get_json() == {"status": "deleted"}
    assert client.get(f"/api/app-surfaces/{surface_id}/frame").status_code == 404


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"title": "", "source": "wine:test", "width": 1, "height": 1}, "title is required"),
        ({"title": "x", "source": "", "width": 1, "height": 1}, "source is required"),
        (
            {"title": "x", "source": "wine:test", "width": 0, "height": 1},
            "width must be an integer",
        ),
        (
            {"title": "x", "source": "wine:test", "width": 1, "height": 20000},
            "height must be an integer",
        ),
    ],
)
def test_register_rejects_invalid_surface_metadata(payload, error):
    client = app.test_client()
    response = client.post("/api/app-surfaces", json=payload)
    assert response.status_code == 400
    assert error in response.get_json()["error"]


def test_frame_publish_rejects_wrong_token_content_type_empty_and_oversize(monkeypatch):
    client = app.test_client()
    registered = _register(client)
    surface_id = registered["surface"]["id"]
    token = registered["producer_token"]

    wrong_token = client.put(
        f"/api/app-surfaces/{surface_id}/frame",
        data=b"x",
        headers={"Content-Type": "image/jpeg", "X-Alice-Surface-Token": "wrong"},
    )
    assert wrong_token.status_code == 403

    wrong_type = client.put(
        f"/api/app-surfaces/{surface_id}/frame",
        data=b"x",
        headers={"Content-Type": "image/gif", "X-Alice-Surface-Token": token},
    )
    assert wrong_type.status_code == 400

    empty = client.put(
        f"/api/app-surfaces/{surface_id}/frame",
        data=b"",
        headers={"Content-Type": "image/png", "X-Alice-Surface-Token": token},
    )
    assert empty.status_code == 400

    monkeypatch.setattr("app_surfaces.registry.MAX_FRAME_BYTES", 2)
    too_large = client.put(
        f"/api/app-surfaces/{surface_id}/frame",
        data=b"123",
        headers={"Content-Type": "image/png", "X-Alice-Surface-Token": token},
    )
    assert too_large.status_code == 400


def test_input_validation_and_limit_validation():
    client = app.test_client()
    registered = _register(client)
    surface_id = registered["surface"]["id"]
    token = registered["producer_token"]

    invalid = client.post(
        f"/api/app-surfaces/{surface_id}/input",
        json={"type": "launch_shell"},
    )
    assert invalid.status_code == 400

    bad_limit = client.get(
        f"/api/app-surfaces/{surface_id}/input?limit=nope",
        headers={"X-Alice-Surface-Token": token},
    )
    assert bad_limit.status_code == 400

    no_auth = client.get(f"/api/app-surfaces/{surface_id}/input")
    assert no_auth.status_code == 403


def test_registry_direct_contracts_cover_normalization_and_bounds():
    registry = SurfaceRegistry()
    surface, token = registry.register(
        title="  App  ",
        source="  native:test  ",
        width=320,
        height=240,
    )
    assert surface["title"] == "App"
    assert surface["source"] == "native:test"

    queued = registry.queue_input(
        surface["id"],
        {
            "type": "key_down",
            "key": "A",
            "code": "KeyA",
            "modifiers": ["ctrl"],
            "ignored": "value",
        },
    )
    assert queued["key"] == "A"
    assert queued["code"] == "KeyA"
    assert queued["modifiers"] == ["ctrl"]
    assert "ignored" not in queued

    assert registry.drain_inputs(surface["id"], token, limit=9999) == [queued]

    with pytest.raises(SurfaceAuthError):
        registry.unregister(surface["id"], "wrong")

    registry.unregister(surface["id"], token)
    with pytest.raises(SurfaceNotFound):
        registry.frame(surface["id"])


def test_registry_rejects_non_integer_dimensions_and_missing_frame():
    registry = SurfaceRegistry()

    with pytest.raises(SurfaceValidationError):
        registry.register(title="x", source="native", width="800", height=600)

    surface, _token = registry.register(
        title="x",
        source="native",
        width=800,
        height=600,
    )
    with pytest.raises(SurfaceNotFound):
        registry.frame(surface["id"])


def test_registry_frame_size_constant_remains_bounded():
    assert MAX_FRAME_BYTES == 8 * 1024 * 1024
