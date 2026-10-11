"""Local listing stays within its configured tree, including during path replacement."""

import errno
import os

from flask import Flask
import pytest

import file_routes


@pytest.fixture
def listing(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    monkeypatch.setattr(file_routes, "LOCAL_REPO_DIR", str(root))
    app = Flask(__name__)
    app.register_blueprint(file_routes.file_bp)
    return app.test_client(), root, outside


def test_allowed_root_and_nested_directory(listing):
    client, root, _ = listing
    (root / "nested").mkdir()
    (root / "nested" / "ok.txt").write_text("hello")
    assert client.get("/api/local-files").json["items"] == [
        {"name": "nested", "is_dir": True, "size": 0}
    ]
    response = client.get("/api/local-files", query_string={"path": "nested/"})
    assert response.status_code == 200
    assert response.json == {
        "success": True,
        "path": str(root / "nested"),
        "items": [{"name": "ok.txt", "is_dir": False, "size": 5}],
    }


@pytest.mark.parametrize("path", ["../outside", "nested/../../outside", "/etc", "\x00"])
def test_invalid_paths_are_rejected(listing, path):
    client, _, _ = listing
    assert client.get("/api/local-files", query_string={"path": path}).status_code == 400


@pytest.mark.parametrize("nested", [False, True])
def test_outside_symlink_is_rejected(listing, nested):
    client, root, outside = listing
    parent = root / "nested" if nested else root
    parent.mkdir(exist_ok=True)
    (parent / "escape").symlink_to(outside, target_is_directory=True)
    path = "nested/escape" if nested else "escape"
    response = client.get("/api/local-files", query_string={"path": path})
    assert response.status_code == 400
    assert "private.txt" not in response.get_data(as_text=True)


def test_in_root_symlink_remains_navigable(listing):
    client, root, _ = listing
    (root / "real").mkdir()
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    response = client.get("/api/local-files", query_string={"path": "alias"})
    assert response.status_code == 200
    assert response.json["items"] == []


def test_listing_does_not_follow_symlink_metadata(listing):
    client, root, outside = listing
    (root / "link").symlink_to(outside / "private.txt")
    assert client.get("/api/local-files").json["items"] == [
        {"name": "link", "is_dir": False, "size": 0}
    ]


@pytest.mark.parametrize("component", ["nested", "leaf"])
def test_component_swap_after_resolution_cannot_escape(listing, monkeypatch, component):
    client, root, outside = listing
    (root / "nested" / "leaf").mkdir(parents=True)
    original = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == component and kwargs.get("dir_fd") is not None and not swapped:
            source = root / "nested" if component == "nested" else root / "nested" / "leaf"
            source.rename(source.with_name("moved"))
            source.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_routes.os, "open", swapping_open)
    response = client.get("/api/local-files", query_string={"path": "nested/leaf"})
    assert swapped
    assert response.status_code == 400
    assert "private.txt" not in response.get_data(as_text=True)


def test_path_swap_after_open_lists_original_descriptor(listing, monkeypatch):
    client, root, outside = listing
    (root / "nested").mkdir()
    (root / "nested" / "ok.txt").write_text("ok")
    original = os.scandir

    def swapping_scandir(fd):
        assert isinstance(fd, int)
        (root / "nested").rename(root / "moved")
        (root / "nested").symlink_to(outside, target_is_directory=True)
        return original(fd)

    monkeypatch.setattr(file_routes.os, "scandir", swapping_scandir)
    response = client.get("/api/local-files", query_string={"path": "nested"})
    assert response.status_code == 200
    assert [item["name"] for item in response.json["items"]] == ["ok.txt"]


def test_missing_directory_returns_404_without_error_details(listing):
    client, root, _ = listing
    response = client.get("/api/local-files", query_string={"path": "missing"})
    assert response.status_code == 404
    assert str(root) not in response.get_data(as_text=True)


def test_scandir_failure_closes_descriptors_and_sanitizes_error(listing, monkeypatch):
    client, root, _ = listing
    (root / "nested").mkdir()
    opened = []
    original = os.open

    def tracking_open(*args, **kwargs):
        fd = original(*args, **kwargs)
        opened.append(fd)
        return fd

    def fail(_fd):
        raise PermissionError("sensitive filesystem detail")

    monkeypatch.setattr(file_routes.os, "open", tracking_open)
    monkeypatch.setattr(file_routes.os, "scandir", fail)
    response = client.get("/api/local-files", query_string={"path": "nested"})
    assert response.status_code == 500
    assert "sensitive" not in response.get_data(as_text=True)
    assert opened
    for fd in opened:
        with pytest.raises(OSError) as error:
            os.fstat(fd)
        assert error.value.errno == errno.EBADF


def test_platform_without_nofollow_fails_closed(listing, monkeypatch):
    client, _, _ = listing
    monkeypatch.delattr(file_routes.os, "O_NOFOLLOW")
    response = client.get("/api/local-files")
    assert response.status_code == 500
    assert "O_NOFOLLOW" not in response.get_data(as_text=True)


def test_missing_root_returns_404(listing):
    client, root, _ = listing
    root.rmdir()
    assert client.get("/api/local-files").status_code == 404


def test_symlink_loop_is_rejected(listing):
    client, root, _ = listing
    (root / "loop").symlink_to(root / "loop")
    assert client.get("/api/local-files", query_string={"path": "loop"}).status_code == 400
