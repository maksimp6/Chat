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
            if method == "GET" and path.endswith("/git/ref/heads/logos-3d-previews"):
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
            if method == "PATCH" and path.endswith("/git/refs/heads/logos-3d-previews"):
                assert body == {"sha": "f" * 40, "force": False}
                return {"ref": "refs/heads/logos-3d-previews"}
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
    assert "schedule:" in trusted
    assert "cron:" in trusted
    assert "workflow_dispatch:" in trusted
    assert "workflow_run:" not in trusted
    assert "pull_request_target:" not in trusted
    assert ("ref: $" + "{{ github.event.repository.default_branch }}") in trusted
    assert "persist-credentials: false" in trusted
    assert "contents: write" in trusted and "issues: write" in trusted
    assert "printing3d/models/**" in tests
    assert "actions/checkout@" in trusted and "scripts.publish_3d_previews" in trusted
    assert "GITHUB_TOKEN" not in tests and "contents: write" not in tests
    assert "Pillow==12.3.0" in tests
    assert "scripts/validation/render_preview_contract.py" in tests


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


@pytest.mark.parametrize(
    "data",
    [
        b"solid bad\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendsolid\n",
        b"solid bad\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\n",
    ],
)
def test_ascii_stl_requires_complete_facets(data):
    with pytest.raises(PreviewError):
        load_mesh(data, "truncated.stl")


def test_utf16_xml_entities_and_compression_bombs_are_rejected():
    for source in [
        ('<!DOCTYPE model [<!ENTITY x "injected">]>' + f'<model xmlns="{CORE}">&x;</model>').encode(
            "utf-16"
        ),
        b" " * 1_000_000,
    ]:
        bio = BytesIO()
        with ZipFile(bio, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("3D/3dmodel.model", source)
        with pytest.raises(PreviewError):
            load_mesh(bio.getvalue(), "bomb.3mf")


def test_nonvertex_nodes_are_not_geometry():
    bad = f'<model xmlns="{CORE}"><resources><object id="1"><mesh>{VERTICES.replace("vertex ", "evil ")}{TRIS}</mesh></object></resources><build><item objectid="1"/></build></model>'
    bio = BytesIO()
    with ZipFile(bio, "w") as archive:
        archive.writestr("3D/3dmodel.model", bad)
    with pytest.raises(PreviewError, match="vertex element"):
        load_mesh(bio.getvalue(), "evil.3mf")


@pytest.mark.parametrize(
    "path", ["printing3d/assets/./a.stl", "printing3d/assets//a.stl", "printing3d/assets/../a.stl"]
)
def test_catalog_path_requires_canonical_name(path):
    assert not publication.safe_model_path(path)


def test_catalog_models_are_eligible_without_copying_sources():
    assert publication.safe_model_path(
        "printing3d/assets/oem_spindle_v03/parts/a1mini_oem_wide_hub_HALF_A.stl"
    )
    assert not publication.safe_model_path("printing3d/assets/source/generator.py.txt")


def test_credential_free_worker_limits_and_determinism(monkeypatch):
    from scripts import render_3d_worker as worker

    original = worker.subprocess.run
    calls = []

    def checked(*args, **kwargs):
        calls.append(kwargs)
        assert "GH_TOKEN" not in kwargs["env"]
        assert kwargs["timeout"] == 30
        assert "-I" in args[0]
        return original(*args, **kwargs)

    monkeypatch.setenv("GH_TOKEN", "test-secret-never-in-output")
    monkeypatch.setattr(worker.subprocess, "run", checked)
    metadata, png = worker.bounded_render(tetra_stl(), "tetra.stl")
    assert metadata["triangles"] == 4
    assert png == worker.bounded_render(tetra_stl(), "tetra.stl")[1]
    assert b"test-secret-never-in-output" not in png
    with pytest.raises(PreviewError):
        worker.bounded_render(b"broken", "tetra.stl")
    assert calls


def test_worker_timeout_reports_unavailable(monkeypatch):
    from scripts import render_3d_worker as worker

    def timeout(*args, **kwargs):
        raise worker.subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(worker.subprocess, "run", timeout)
    with pytest.raises(PreviewError, match="time budget"):
        worker.bounded_render(tetra_stl(), "tetra.stl")


def test_token_cannot_follow_api_redirect():
    handler = publication.NoRedirect()
    with pytest.raises(publication.PublishError, match="redirect"):
        handler.redirect_request(None, None, 302, "found", {}, "https://evil.example/")


def test_head_change_during_render_does_not_publish_stale_comment(monkeypatch):
    api = StubGitHub()

    def render(*args):
        api.head = "2" * 40
        return "stale report"

    monkeypatch.setattr(publication, "render_report", render)
    assert publication.run(api, 1118) == "stale_head"
    assert not api.comments


def test_new_preview_branch_is_orphan_image_only():
    calls = []

    class API:
        prefix = "/repos/maksimp6/Chat"

        def json(self, method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                assert path.endswith("/git/ref/heads/logos-3d-previews")
                raise publication.PublishError("GitHub returned HTTP 404")
            if path.endswith("/git/trees"):
                assert "base_tree" not in body
                assert all(
                    item["path"].startswith(publication.OUTPUT_PREFIX) for item in body["tree"]
                )
            if path.endswith("/git/commits"):
                assert body["parents"] == []
            return {"sha": "a" * 40}

    assert (
        publication.publish_images(
            API(),
            [
                (
                    "printing3d/previews/pr-1/model.stl.png",
                    render_preview(load_mesh(tetra_stl(), "model.stl")),
                )
            ],
        )
        == "a" * 40
    )
    assert calls[-1][2]["ref"] == "refs/heads/logos-3d-previews"
    assert not any("master" in path or "develop" in path for _, path, _ in calls)


def test_refresh_does_not_commit_identical_image_tree():
    class API:
        prefix = "/repos/maksimp6/Chat"

        def json(self, method, path, body=None):
            if path.endswith("/git/ref/heads/logos-3d-previews"):
                return {"object": {"sha": "a" * 40}}
            if path.endswith("/git/commits/" + "a" * 40):
                return {"tree": {"sha": "b" * 40}}
            if path.endswith("/git/blobs"):
                return {"sha": "c" * 40}
            if path.endswith("/git/trees"):
                return {"sha": "b" * 40}
            raise AssertionError("unchanged PNG must not commit/update ref")

    assert (
        publication.publish_images(
            API(),
            [
                (
                    "printing3d/previews/pr-1/model.stl.png",
                    render_preview(load_mesh(tetra_stl(), "model.stl")),
                )
            ],
        )
        == "a" * 40
    )


@pytest.mark.parametrize(
    "target", ["printing3d/previews/../secrets.png", ".github/workflows/run.png"]
)
def test_publication_rejects_unsafe_destination(target):
    class API:
        prefix = "/repos/maksimp6/Chat"

        def json(self, method, path, body=None):
            if path.endswith("/git/ref/heads/logos-3d-previews"):
                return {"object": {"sha": "a" * 40}}
            if path.endswith("/git/commits/" + "a" * 40):
                return {"tree": {"sha": "b" * 40}}
            raise AssertionError("unsafe output must not write")

    with pytest.raises(publication.PublishError, match="destination"):
        publication.publish_images(API(), [(target, b"\x89PNG\r\n\x1a\n")])


def test_schedule_scans_only_same_repo_prs_and_deduplicates(monkeypatch):
    """One cron scan may publish images; repeated scans cannot flood comments."""
    api = StubGitHub()
    base = api.json
    scan_calls = []

    def with_catalog(method, path, body=None):
        if method == "GET" and path.endswith(
            "/pulls?state=open&sort=updated&direction=desc&per_page=100"
        ):
            scan_calls.append(1)
            return [
                {"number": 1118, "head": {"repo": {"full_name": api.repo}}},
                {"number": 1119, "head": {"repo": {"full_name": "fork/untrusted"}}},
            ]
        return base(method, path, body)

    api.json = with_catalog
    published = []
    monkeypatch.setattr(
        publication,
        "publish_images",
        lambda _api, images: published.append(images) or "a" * 40,
    )
    assert publication.scan_open_prs(api) == "scan_checked=1 preview_comments_changed=1"
    assert len(api.comments) == 1
    assert publication.scan_open_prs(api) == "scan_checked=1 preview_comments_changed=0"
    assert len(api.comments) == 1
    assert len(published) == 1
    assert len(scan_calls) == 2


def test_schedule_fails_closed_on_unreadable_pr_catalog():
    api = StubGitHub()

    def invalid_catalog(method, path, body=None):
        if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=100"):
            return {"error": "unexpected"}
        raise AssertionError((method, path))

    api.json = invalid_catalog
    with pytest.raises(publication.PublishError, match="invalid open-PR"):
        publication.scan_open_prs(api)


def test_schedule_rejects_partial_failure_instead_of_faking_success():
    api = StubGitHub()
    original = api.json

    def with_catalog(method, path, body=None):
        if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=100"):
            return [{"number": 1118, "head": {"repo": {"full_name": api.repo}}}]
        if path.endswith("/git/blobs/" + api.change[0]["sha"]):
            return {"encoding": "not-supported", "size": 4}
        return original(method, path, body)

    api.json = with_catalog
    with pytest.raises(publication.PublishError, match="preview publication"):
        publication.scan_open_prs(api)
    assert not api.comments


def test_schedule_ignores_pr_which_is_not_in_same_repo():
    api = StubGitHub()

    def catalog(method, path, body=None):
        if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=100"):
            return [{"number": 1118, "head": {"repo": {"full_name": "not/ours"}}}]
        raise AssertionError("Should never inspect a fork on this scheduled scan")

    api.json = catalog
    assert publication.scan_open_prs(api) == "scan_checked=0 preview_comments_changed=0"


@pytest.mark.parametrize("count", [100, 101, 112, 300])
def test_open_pr_catalog_reads_all_bounded_pages_before_processing(count, monkeypatch):
    api = StubGitHub()
    entries = [{"number": i + 1, "head": {"repo": {"full_name": api.repo}}} for i in range(count)]
    reads = []
    processed = []

    def pages(method, path, body=None):
        assert method == "GET"
        page = int(path.rsplit("&page=", 1)[-1]) if "&page=" in path else 1
        reads.append(page)
        return entries[(page - 1) * 100 : page * 100]

    def run(_api, number):
        assert reads[-1] == count // 100 + 1
        processed.append(number)
        return "unchanged"

    api.json = pages
    monkeypatch.setattr(publication, "run", run)
    assert publication.scan_open_prs(api) == f"scan_checked={count} preview_comments_changed=0"
    assert processed == list(range(1, count + 1))


def test_scan_overflow_never_claims_complete_or_processes_partial_catalog(monkeypatch):
    api = StubGitHub()
    entries = [{"number": i + 1} for i in range(301)]

    def pages(method, path, body=None):
        page = int(path.rsplit("&page=", 1)[-1]) if "&page=" in path else 1
        return entries[(page - 1) * 100 : page * 100]

    api.json = pages
    monkeypatch.setattr(publication, "run", lambda *args: pytest.fail("partial scan must not run"))
    with pytest.raises(publication.PublishError, match="scan incomplete"):
        publication.scan_open_prs(api)


def test_unstable_pagination_is_explicit_failure():
    api = StubGitHub()
    page = [{"number": i + 1} for i in range(100)]
    api.json = lambda *args: page
    with pytest.raises(publication.PublishError, match="unstable"):
        publication._open_pr_catalog(api)


def test_scan_wall_budget_returns_incomplete(monkeypatch):
    api = StubGitHub()
    api.json = lambda *args: [{"number": 1118}]
    times = iter([0, publication.SCAN_SECONDS + 1])
    monkeypatch.setattr(publication.time, "monotonic", lambda: next(times))
    with pytest.raises(publication.PublishError, match="time budget"):
        publication.scan_open_prs(api)


def test_fifteen_mesh_pilot_reports_all_paths_with_only_three_renders(monkeypatch):
    api = StubGitHub()
    source = api.change[0]["sha"]
    api.change = [
        {"status": "added", "filename": f"printing3d/assets/demo/part_{i:02d}.stl", "sha": source}
        for i in range(15)
    ]
    renders = []
    original = publication.bounded_render

    def render(raw, filename):
        renders.append(filename)
        return original(raw, filename)

    monkeypatch.setattr(publication, "bounded_render", render)
    published = []
    monkeypatch.setattr(
        publication, "publish_images", lambda _api, images: published.append(images) or "a" * 40
    )
    assert publication.run(api, 1118) == "created"
    report = api.comments[0]["body"]
    assert len(renders) == 3 and len(published[0]) == 3
    assert "3 selected, 12 not rendered" in report
    assert report.count("preview unavailable (per-PR budget") == 12
    assert "will not be rendered automatically" in report
    for i in range(15):
        assert f"part_{i:02d}.stl" in report
    assert publication.run(api, 1118) == "unchanged"
    assert len(api.comments) == 1 and len(renders) == 3
    assert len(published) == 1
