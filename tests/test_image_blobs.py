import base64
import stat

import pytest

from chat import image_blobs, image_routes


def _data_url(payload: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def test_large_picture_is_kept_as_file_and_linked_by_id(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))
    payload = b"\x89PNG" + b"0" * image_blobs.INLINE_LIMIT_CHARS

    url = image_routes._image_url(_data_url(payload), "conv-1")

    assert url.startswith("/api/images/blobs/")
    assert url.endswith("?conversation_id=conv-1")
    blob_id = url.removeprefix("/api/images/blobs/").split("?", 1)[0]
    assert image_blobs.load_image(blob_id, "conv-1") == ("image/png", payload)
    assert image_blobs.load_image(blob_id, "another-conv") is None
    saved = next(tmp_path.glob("*/*.png"))
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_small_picture_stays_inline(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))
    small = _data_url(b"tiny")

    assert image_routes._image_url(small, "conv-1") == small
    assert list(tmp_path.iterdir()) == []


def test_blob_ids_are_validated_before_touching_the_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))

    assert image_blobs.load_image("../../etc/passwd", "conv-1") is None
    assert image_blobs.load_image("", "conv-1") is None


def test_blob_route_serves_only_to_the_owning_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))
    from app import app

    monkeypatch.setattr(
        image_routes, "check_access", lambda conv_id, owner_id: conv_id == "conv-1"
    )
    monkeypatch.setattr(image_routes, "get_current_owner_id", lambda: "owner-1")
    blob_id = image_blobs.save_image(_data_url(b"png-bytes"), "conv-1")

    with app.test_client() as client:
        allowed = client.get(f"/api/images/blobs/{blob_id}?conversation_id=conv-1")
        denied = client.get(f"/api/images/blobs/{blob_id}?conversation_id=other")

    assert allowed.status_code == 200
    assert allowed.data == b"png-bytes"
    assert allowed.mimetype == "image/png"
    assert denied.status_code == 404


def test_existing_other_conversation_cannot_claim_an_image(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))
    from app import app

    monkeypatch.setattr(image_routes, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(image_routes, "get_current_owner_id", lambda: "shared-owner")
    blob_id = image_blobs.save_image(_data_url(b"original-picture"), "conv-1")

    response = app.test_client().get(f"/api/images/blobs/{blob_id}?conversation_id=conv-2")

    assert response.status_code == 404


def test_failed_image_write_does_not_publish_partial_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path))

    def fail_fsync(descriptor):
        raise OSError("disk unavailable")

    monkeypatch.setattr(image_blobs.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk unavailable"):
        image_blobs.save_image(_data_url(b"picture"), "conv-1")

    assert list(tmp_path.rglob("*.png")) == []
    assert list(tmp_path.rglob(".image-*")) == []
