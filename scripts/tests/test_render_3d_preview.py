"""Real-geometry preview and trusted GitHub publication contract tests."""

from __future__ import annotations

import base64
import hashlib
import struct
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from scripts import publish_3d_previews as publication
from scripts.render_3d_preview import PreviewError, load_mesh, render_preview


def tetra_stl() -> bytes:
    vertices = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (0.0, 10.0, 0.0),
        (0.0, 0.0, 10.0),
    ]
    faces = [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)]
    blob = bytearray(b"binary-mesh".ljust(80, b"\0") + struct.pack("<I", len(faces)))
    for face in faces:
        blob.extend(
            struct.pack(
                "<12fH", *(0.0, 0.0, 1.0), *(coord for i in face for coord in vertices[i]), 0
            )
        )
    return bytes(blob)


CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
VERTICES = '<vertices><vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/><vertex x="0" y="10" z="0"/></vertices>'
TRIS = '<triangles><triangle v1="0" v2="1" v3="2"/></triangles>'


def real_3mf(
    *, malformed: str | None = None, duplicate: bool = False, cycle: bool = False
) -> bytes:
    parent = (
        f'<model xmlns="{CORE}" xmlns:p="{PRODUCTION}" unit="millimeter">'
        '<resources><object id="2"><components><component objectid="1" '
        'p:path="/3D/Objects/object_1.model" transform="1 0 0 0 1 0 0 0 1 0 0 3"'
        "/></components></object></resources>"
        '<build><item objectid="2"/></build></model>'
    )
    if cycle:
        parent = (
            f'<model xmlns="{CORE}"><resources><object id="2">'
            '<components><component objectid="2"/></components></object></resources>'
            '<build><item objectid="2"/></build></model>'
        )
    child = (
        f'<model xmlns="{CORE}"><resources><object id="1"><mesh>'
        f"{VERTICES}{TRIS}</mesh></object></resources><build/></model>"
    )
    if malformed:
        child = malformed
    bio = BytesIO()
    with ZipFile(bio, "w", compression=ZIP_DEFLATED) as output:
        output.writestr("3D/3dmodel.model", parent)
        output.writestr("3D/Objects/object_1.model", child)
        if duplicate:
            with pytest.warns(UserWarning):
                output.writestr("3D/Objects/object_1.model", child)
    return bio.getvalue()


def test_binary_stl_geometry_and_deterministic_three_view_png():
    data = tetra_stl()
    mesh = load_mesh(data, "tetra.stl")
    assert len(mesh.triangles) == 4
    assert mesh.source_sha256 == hashlib.sha256(data).hexdigest()
    assert mesh.maximum == (10.0, 10.0, 10.0)
    assert mesh.units == "STL units unspecified"
    first = render_preview(mesh)
    assert first == render_preview(mesh)
    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(first)) as image:
        assert image.size == (1240, 490)
        assert image.mode == "RGB"
    assert len(first) < 1_200_000


def test_real_3mf_external_component_and_build_transform():
    mesh = load_mesh(real_3mf(), "part.gcode.3mf")
    assert len(mesh.triangles) == 1
    assert mesh.minimum == (0.0, 0.0, 3.0)
    assert mesh.maximum == (10.0, 10.0, 3.0)
    assert mesh.units == "mm"
    assert render_preview(mesh).startswith(b"\x89PNG")


def test_ascii_stl_and_malformed_stl_fail_closed():
    ascii_data = b"solid tetra\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 2 0 0\nvertex 0 2 0\nendloop\nendfacet\nendsolid\n"
    assert len(load_mesh(ascii_data, "ascii.stl").triangles) == 1
    for invalid in (
        b"",
        b"solid broken\nvertex 0 1 2\n",
        b"fake-binary",
        b"solid broken\nvertex nan 1 2\n",
    ):
        with pytest.raises(PreviewError):
            load_mesh(invalid, "broken.stl")


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.stl",
        "/etc/password.stl",
        "nested/model.stl",
        "file.png",
        "file\nhack.stl",
        ".hidden.stl",
        "a" * 160 + ".stl",
    ],
)
def test_invalid_mesh_filename_is_rejected(filename):
    with pytest.raises(PreviewError):
        load_mesh(tetra_stl(), filename)


@pytest.mark.parametrize(
    "bad",
    [
        real_3mf(duplicate=True),
        real_3mf(cycle=True),
        real_3mf(malformed="<wrong-ns/>"),
        real_3mf(malformed='<!DOCTYPE x [ <!ENTITY a "bad"> ]><x>&a;</x>'),
    ],
)
def test_malformed_3mf_is_fail_closed(bad):
    with pytest.raises(PreviewError):
        load_mesh(bad, "part.3mf")


def test_oversized_source_or_nan_mesh_is_rejected(monkeypatch):
    import scripts.render_3d_preview as renderer

    with pytest.raises(PreviewError, match="size budget"):
        load_mesh(b"\0" * (renderer.MAX_SOURCE_BYTES + 1), "huge.stl")
    corrupted = bytearray(tetra_stl())
    struct.pack_into("<f", corrupted, 84 + 12, float("nan"))
    with pytest.raises(PreviewError, match="nonfinite"):
        load_mesh(bytes(corrupted), "bad.stl")
    monkeypatch.setattr(renderer, "MAX_TRIANGLES", 2)
    with pytest.raises(PreviewError, match="triangle limit"):
        load_mesh(tetra_stl(), "too-many.stl")


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("printing3d/models/thing.stl", True),
        ("printing3d/models/demo/model.gcode.3mf", True),
        ("printing3d/models/folder/part.3mf", True),
        ("other/model.stl", False),
        ("printing3d/models/../../.github/secrets.stl", False),
        ("printing3d/models/file.png", False),
        ("printing3d/models/model.stl;rm-rf", False),
        ("printing3d/models/bad\n[image](url).stl", False),
    ],
)
def test_preview_models_path_allowlist(path, allowed):
    assert publication.safe_model_path(path) is allowed


def test_pr_file_limit_and_blob_sha_verification():
    with pytest.raises(publication.PublishError, match="too many"):
        publication.select_models(
            [
                {"status": "added", "filename": f"printing3d/models/file_{i}.stl", "sha": "a" * 40}
                for i in range(publication.MAX_MODELS + 1)
            ]
        )
    good = tetra_stl()
    key = hashlib.sha1(b"blob " + str(len(good)).encode() + b"\0" + good).hexdigest()

    class FakeAPI:
        prefix = "/repos/maksimp6/Chat"
        repo = "maksimp6/Chat"

        def json(self, method, path, body=None):
            assert method == "GET"
            assert path.endswith("/git/blobs/" + key)
            return {
                "size": len(good),
                "encoding": "base64",
                "content": base64.b64encode(good).decode(),
            }

    assert publication._blob(FakeAPI(), key) == good


class StubGitHub:
    prefix = "/repos/maksimp6/Chat"
    repo = "maksimp6/Chat"

    def __init__(self):
        self.head = "1" * 40
        self.fork = False
        self.comments = []
        self.writes = []
        self.change = [
            {
                "status": "added",
                "filename": "printing3d/models/demo.stl",
                "sha": hashlib.sha1(
                    b"blob " + str(len(tetra_stl())).encode() + b"\0" + tetra_stl()
                ).hexdigest(),
            }
        ]

    def json(self, method, path, body=None):
        self.writes.append((method, path, body))
        if method == "GET" and path.endswith("/pulls/1118"):
            return {
                "changed_files": len(self.change),
                "state": "open",
                "head": {
                    "sha": self.head,
                    "repo": {"full_name": "forker/evil" if self.fork else self.repo},
                },
            }
        if method == "GET" and "/pulls/1118/files" in path:
            return self.change
        if method == "GET" and "/git/blobs/" in path:
            raw = tetra_stl()
            return {
                "size": len(raw),
                "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
            }
        if method == "GET" and "/issues/1118/comments" in path:
            return self.comments
        if method == "POST" and path.endswith("/issues/1118/comments"):
            self.comments.append(
                {"id": 42, "user": {"login": "github-actions[bot]"}, "body": body["body"]}
            )
            return {"id": 42}
        if method == "PATCH" and path.endswith("/issues/comments/42"):
            self.comments[0]["body"] = body["body"]
            return {"id": 42}
        raise AssertionError((method, path))


def test_one_auto_comment_per_head_and_manual_refresh(monkeypatch):
    api = StubGitHub()
    published = []
    monkeypatch.setattr(
        publication, "publish_images", lambda _api, content: published.append(content) or ("a" * 40)
    )
    assert publication.run(api, 1118) == "created"
    assert len(published) == 1
    body = api.comments[0]["body"]
    assert "![Three real mesh views" in body
    assert "raw.githubusercontent.com/maksimp6/Chat/" + "a" * 40 in body
    assert "geometry only" in body
    assert publication.run(api, 1118) == "unchanged"
    assert len(published) == 1, "duplicate event must not publish another Git commit"
    assert publication.run(api, 1118, refresh=True) == "unchanged"
    assert len(published) == 2, "explicit refresh rerenders but upserts the same bot comment"
    api.head = "2" * 40
    assert publication.run(api, 1118) == "updated"
    assert len(api.comments) == 1


def test_untrusted_fork_or_missing_model_does_not_write():
    api = StubGitHub()
    api.fork = True
    assert publication.run(api, 1118) == "skipped_fork"
    assert not any(method != "GET" for method, _, _ in api.writes)
    api.fork = False
    api.change = [{"status": "modified", "filename": "some/path.txt", "sha": "0" * 40}]
    assert publication.run(api, 1118) == "no_model_changes"
    assert not api.comments


def test_preview_branch_is_explicit_and_never_force_written():
    class MockGit:
        prefix = "/repos/maksimp6/Chat"
        repo = "maksimp6/Chat"

        def __init__(self):
            self.writes = []

        def json(self, method, path, body=None):
            self.writes.append((method, path, body))
            if method == "GET" and path.endswith("/git/ref/heads/alice-3d-previews"):
                return {"object": {"sha": "b" * 40}}
            if method == "GET" and path.endswith("/git/commits/" + "b" * 40):
                return {"tree": {"sha": "c" * 40}}
            if method == "POST" and path.endswith("/git/blobs"):
                return {"sha": "d" * 40}
            if method == "POST" and path.endswith("/git/trees"):
                assert body["base_tree"] == "c" * 40
                return {"sha": "e" * 40}
            if method == "POST" and path.endswith("/git/commits"):
                assert body["parents"] == ["b" * 40]
                return {"sha": "f" * 40}
            if method == "PATCH" and path.endswith("/git/refs/heads/alice-3d-previews"):
                assert body == {"sha": "f" * 40, "force": False}
                return {"ref": "refs/heads/alice-3d-previews"}
            raise AssertionError((method, path))

    api = MockGit()
    path = "printing3d/previews/pr-1118/demo.stl.png"
    assert (
        publication.publish_images(
            api, [(path, render_preview(load_mesh(tetra_stl(), "demo.stl")))]
        )
        == "f" * 40
    )
    assert all(
        "heads/master" not in target for method, target, _ in api.writes if method == "PATCH"
    )


def test_preview_workflows_keep_comment_token_out_of_untrusted_pr_code():
    root = Path(__file__).resolve().parents[2]
    trusted = (root / ".github/workflows/3d-pr-previews.yml").read_text()
    tests = (root / ".github/workflows/3d-render-tests.yml").read_text()
    assert "workflow_run:" in trusted
    assert "pull_request_target:" not in trusted
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in trusted
    assert ("ref: $" + "{{ github.event.repository.default_branch }}") in trusted
    assert "persist-credentials: false" in trusted
    assert "contents: write" in trusted and "issues: write" in trusted
    assert "printing3d/models/**" in tests
    assert "actions/checkout@" in trusted and "scripts.publish_3d_previews" in trusted
    assert "GITHUB_TOKEN" not in tests and "contents: write" not in tests
    assert "Pillow==12.3.0" in tests
    assert "scripts/tests/test_render_3d_preview.py" in tests


def test_sliced_3mf_with_no_geometry_does_not_forge_preview():
    root_xml = f'<model xmlns="{CORE}" unit="millimeter"><resources/><build/></model>'
    bio = BytesIO()
    with ZipFile(bio, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", root_xml)
        archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1")
    with pytest.raises(PreviewError, match="missing build items"):
        load_mesh(bio.getvalue(), "sliced.gcode.3mf")


def test_3mf_outside_part_and_nonfinite_transform_are_rejected():
    other = (
        f'<model xmlns="{CORE}" xmlns:p="{PRODUCTION}"><resources>'
        '<object id="1"><components><component objectid="1" '
        'p:path="/../../private.json"/></components></object></resources>'
        '<build><item objectid="1"/></build></model>'
    )
    broken = BytesIO()
    with ZipFile(broken, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", other)
    with pytest.raises(PreviewError, match="outside"):
        load_mesh(broken.getvalue(), "path.3mf")

    from scripts import render_3d_preview as renderer

    with pytest.raises(PreviewError, match="nonfinite"):
        renderer._transform("nan 0 0 0 1 0 0 0 1 0 0 0")
    with pytest.raises(PreviewError, match="invalid 3MF transform"):
        renderer._transform("1 2 3")


def test_3mf_count_limit_and_unsafe_zip_path_fail_closed():
    from scripts import render_3d_preview as renderer

    bio = BytesIO()
    with ZipFile(bio, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("../private.json", "{}")
    with pytest.raises(PreviewError, match="unsafe"):
        load_mesh(bio.getvalue(), "escape.3mf")

    bio = BytesIO()
    with ZipFile(bio, "w", compression=ZIP_DEFLATED) as archive:
        for number in range(renderer.MAX_MEMBERS + 1):
            archive.writestr(f"3D/models/file-{number}.model", "abc")
    with pytest.raises(PreviewError, match="oversized"):
        load_mesh(bio.getvalue(), "many.3mf")


def test_oversized_pr_change_set_rejects_partial_listing():
    api = StubGitHub()
    api.change = [{"status": "added", "filename": "printing3d/models/tetra.stl", "sha": "a" * 40}]
    original = api.json

    def incomplete(method, path, body=None):
        if path.endswith("/pulls/1118"):
            obj = original(method, path, body)
            obj["changed_files"] = 102
            return obj
        return original(method, path, body)

    api.json = incomplete
    with pytest.raises(publication.PublishError, match="count exceeds"):
        publication.run(api, 1118)
    assert not any(method != "GET" for method, _, _ in api.writes)


def test_comment_marker_from_nonbot_cannot_be_hijacked(monkeypatch):
    api = StubGitHub()
    api.comments.append(
        {"id": 10, "user": {"login": "untrusted-human"}, "body": publication.MARKER}
    )
    posted = []
    monkeypatch.setattr(
        publication, "publish_images", lambda _api, artifacts: posted.append(artifacts) or "a" * 40
    )
    assert publication.run(api, 1118) == "created"
    assert len(api.comments) == 2
    assert api.comments[0]["user"]["login"] == "untrusted-human"
    assert api.comments[1]["user"]["login"] == "github-actions[bot]"


@pytest.mark.parametrize(
    "repo,token",
    [("", "token"), ("not-a-repo", "token"), ("owner/repo", ""), ("owner/repo/extra", "secret")],
)
def test_invalid_github_publication_configuration_is_blocked(repo, token):
    with pytest.raises(publication.PublishError):
        publication.Github(repo, token)


def test_stale_workflow_event_is_noop_before_publication():
    api = StubGitHub()
    assert publication.run(api, 1118, expected_head="9" * 40) == "stale_head"
    assert not api.comments
    assert not any(method != "GET" for method, _, _ in api.writes)


def test_matching_workflow_event_permits_only_own_pr(monkeypatch):
    api = StubGitHub()
    saved = []
    monkeypatch.setattr(
        publication, "publish_images", lambda _api, images: saved.append(images) or "a" * 40
    )
    assert publication.run(api, 1118, expected_head=api.head) == "created"
    assert len(saved) == 1
