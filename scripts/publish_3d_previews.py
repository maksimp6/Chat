"""Trusted workflow publisher: actual mesh PNG previews in a single PR comment.

Never checks out PR code, runs G-code, invokes a slicer or talks to a printer.
Only same-repository changed 3D blobs under printing3d/models/ are eligible.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import PurePosixPath
from urllib import error, parse, request

from scripts.render_3d_preview import PreviewError, load_mesh, render_preview

MARKER = "<!-- alice-3d-preview:v1 -->"
PREVIEW_BRANCH = "alice-3d-previews"
INPUT_PREFIX = "printing3d/models/"
OUTPUT_PREFIX = "printing3d/previews/"
MAX_MODELS = 3
MAX_CHANGED_FILES = 100
MAX_API_BYTES = 25 * 1024 * 1024
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PublishError(ValueError):
    """Cannot safely publish previews from the requested PR."""


class Github:
    def __init__(self, repo: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise PublishError("invalid trusted repository name")
        if not token:
            raise PublishError("GitHub token is unavailable")
        self.repo = repo
        self.prefix = f"/repos/{repo}"
        self.token = token

    def json(self, method: str, path: str, body: object = None) -> object:
        if not path.startswith(self.prefix + "/") and path != self.prefix:
            raise PublishError("GitHub API path escaped repository")
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "alice-pro-3d-preview/1",
        }
        if raw is not None:
            headers["Content-Type"] = "application/json"
        call = request.Request(
            "https://api.github.com" + path, data=raw, headers=headers, method=method
        )
        try:
            with request.urlopen(call, timeout=30) as response:
                response_bytes = response.read(MAX_API_BYTES + 1)
            if len(response_bytes) > MAX_API_BYTES:
                raise PublishError("GitHub response exceeded byte limit")
            return json.loads(response_bytes)
        except error.HTTPError as exc:
            # Never include URLs, tokens, response bodies, or unexpected user strings.
            raise PublishError(f"GitHub returned HTTP {exc.code}") from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise PublishError("GitHub API request or JSON failed") from exc


def safe_model_path(path: object) -> bool:
    """Explicit GitHub-owned models directory, simple regular names only."""
    if not isinstance(path, str) or not path.startswith(INPUT_PREFIX) or len(path) > 240:
        return False
    pieces = PurePosixPath(path).parts
    prefix_parts = PurePosixPath(INPUT_PREFIX).parts
    if pieces[: len(prefix_parts)] != prefix_parts:
        return False
    remainder = pieces[len(prefix_parts) :]
    if not 1 <= len(remainder) <= 3:
        return False
    if any(not PATH_RE.fullmatch(chunk) for chunk in remainder[:-1]):
        return False
    return bool(
        NAME_RE.fullmatch(remainder[-1]) and remainder[-1].lower().endswith((".stl", ".3mf"))
    )


def _sha(value: object) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PublishError("invalid PR or blob SHA")
    return value


def select_models(changed: list[dict]) -> list[dict]:
    if len(changed) > MAX_CHANGED_FILES:
        raise PublishError("PR changed-file list exceeds review cap")
    files = [
        item
        for item in changed
        if item.get("status") in ("added", "modified", "renamed")
        and safe_model_path(item.get("filename"))
    ]
    if len(files) > MAX_MODELS:
        raise PublishError("PR adds too many 3D model previews")
    for item in files:
        _sha(item.get("sha"))
    return sorted(files, key=lambda item: item["filename"])


def _blob(api: Github, sha: str) -> bytes:
    response = api.json("GET", api.prefix + "/git/blobs/" + _sha(sha))
    if not isinstance(response, dict) or response.get("encoding") != "base64":
        raise PublishError("GitHub blob encoding not supported")
    reported_size = response.get("size")
    if not isinstance(reported_size, int) or not 0 < reported_size <= 16 * 1024 * 1024:
        raise PublishError("PR model blob exceeds preview limit")
    encoded = response.get("content")
    if not isinstance(encoded, str) or len(encoded) > 23 * 1024 * 1024:
        raise PublishError("PR model blob encoding oversized")
    try:
        raw = base64.b64decode(encoded, validate=False)
    except ValueError as exc:
        raise PublishError("invalid GitHub blob data") from exc
    if len(raw) != reported_size:
        raise PublishError("GitHub model blob length mismatch")
    object_hash = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if object_hash != sha:
        raise PublishError("GitHub model blob SHA mismatch")
    return raw


def _preview_path(pr: int, path: str) -> str:
    source = PurePosixPath(path).name
    digest = hashlib.sha256(path.encode()).hexdigest()[:12]
    return f"{OUTPUT_PREFIX}pr-{pr}/{digest}-{source}.png"


def _ref(api: Github, path: str) -> dict | None:
    try:
        value = api.json("GET", api.prefix + "/git/ref/heads/" + path)
    except PublishError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    if not isinstance(value, dict) or not isinstance(value.get("object"), dict):
        raise PublishError("invalid GitHub ref response")
    return value


def publish_images(api: Github, artifacts: list[tuple[str, bytes]]) -> str:
    """Commit to an unprotected image-only branch. Never update master/develop."""
    if not artifacts:
        raise PublishError("cannot publish an empty image set")
    reference = _ref(api, PREVIEW_BRANCH)
    if reference is None:
        main_ref = _ref(api, "master")
        if main_ref is None:
            raise PublishError("repository master branch missing")
        base_sha = _sha(main_ref["object"]["sha"])
        api.json(
            "POST",
            api.prefix + "/git/refs",
            {"ref": "refs/heads/" + PREVIEW_BRANCH, "sha": base_sha},
        )
    else:
        base_sha = _sha(reference["object"]["sha"])
    prior = api.json("GET", api.prefix + "/git/commits/" + base_sha)
    if not isinstance(prior, dict) or not isinstance(prior.get("tree"), dict):
        raise PublishError("invalid preview parent commit")
    elements = []
    for target, png in artifacts:
        if (
            not target.startswith(OUTPUT_PREFIX)
            or len(png) > 1_200_000
            or not png.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise PublishError("non-PNG or unapproved destination")
        blob = api.json(
            "POST",
            api.prefix + "/git/blobs",
            {"content": base64.b64encode(png).decode("ascii"), "encoding": "base64"},
        )
        if not isinstance(blob, dict):
            raise PublishError("GitHub did not store preview blob")
        elements.append(
            {"path": target, "mode": "100644", "type": "blob", "sha": _sha(blob.get("sha"))}
        )
    tree = api.json(
        "POST",
        api.prefix + "/git/trees",
        {"base_tree": _sha(prior["tree"]["sha"]), "tree": elements},
    )
    if not isinstance(tree, dict):
        raise PublishError("GitHub did not create preview tree")
    new_commit = api.json(
        "POST",
        api.prefix + "/git/commits",
        {
            "message": "preview(3d): publish bounded mesh review images",
            "tree": _sha(tree.get("sha")),
            "parents": [base_sha],
        },
    )
    if not isinstance(new_commit, dict):
        raise PublishError("GitHub did not create preview commit")
    commit_sha = _sha(new_commit.get("sha"))
    # Reject branch races; retry on a later workflow, never force-rewrite.
    api.json(
        "PATCH",
        api.prefix + "/git/refs/heads/" + PREVIEW_BRANCH,
        {"sha": commit_sha, "force": False},
    )
    return commit_sha


def _url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{_sha(sha)}/{parse.quote(path, safe='/.-_')}"


def render_report(api: Github, pr_number: int, sha: str, models: list[dict]) -> str:
    """Publish successful PNGs and return the bot-owned Markdown comment body."""
    summary = [
        MARKER,
        f"<!-- alice-3d-preview:head:{_sha(sha)} -->",
        "### 3D model previews · Alice Pro",
        rf"PR head: `{_sha(sha)[:12]}` · *geometry only, not print approval*.",
    ]
    artifact_pngs = []
    images = []
    for model in models:
        path = model["filename"]
        filename = PurePosixPath(path).name
        source_sha = _sha(model["sha"])
        try:
            mesh = load_mesh(_blob(api, source_sha), filename)
            png = render_preview(mesh)
        except (PreviewError, PublishError) as exc:
            # A misleading image is worse than an explicit unavailable result.
            images.append((path, None, str(exc), None))
            continue
        destination = _preview_path(pr_number, path)
        artifact_pngs.append((destination, png))
        images.append((path, destination, mesh.source_sha256, mesh))
    preview_sha = publish_images(api, artifact_pngs) if artifact_pngs else None
    for path, rendered, digest_or_error, mesh in images:
        source_url = f"https://github.com/{api.repo}/blob/{sha}/{parse.quote(path, safe='/.-_')}"
        source_name = PurePosixPath(path).name
        summary.append(f"\n**[{source_name}]({source_url})**")
        if rendered is None:
            summary.append(rf"Preview unavailable: `{digest_or_error[:140]}`.")
            continue
        dimensions = tuple(
            round(mesh.maximum[index] - mesh.minimum[index], 2) for index in range(3)
        )
        summary.extend(
            [
                rf"Mesh SHA-256: `{digest_or_error}` · {len(mesh.triangles)} triangles · {dimensions} {mesh.units}",
                f"![Three real mesh views: {source_name}]({_url(api.repo, preview_sha, rendered)})",
            ]
        )
    summary.append(
        "\n*These images are derived from triangle geometry. They do not verify slicer settings, "
        "manifoldness, tolerances, mechanical strength, filament or finished-print quality.*"
    )
    return "\n".join(summary)


def _bot_comment(api: Github, pr: int) -> dict | None:
    """Find only our GitHub Actions bot's marker across bounded pagination."""
    existing = None
    for page in range(1, 11):
        comments = api.json("GET", api.prefix + f"/issues/{pr}/comments?per_page=100&page={page}")
        if not isinstance(comments, list):
            raise PublishError("invalid PR comments response")
        for comment in comments:
            user = comment.get("user") or {}
            if user.get("login") == "github-actions[bot]" and MARKER in comment.get("body", ""):
                existing = comment
                break
        if existing or len(comments) < 100:
            break
    else:
        raise PublishError("PR comment pagination limit reached")
    return existing


def upsert_comment(api: Github, pr: int, body: str) -> str:
    existing = _bot_comment(api, pr)
    if existing:
        if existing.get("body") == body:
            return "unchanged"
        api.json("PATCH", api.prefix + "/issues/comments/" + str(existing["id"]), {"body": body})
        return "updated"
    api.json("POST", api.prefix + f"/issues/{pr}/comments", {"body": body})
    return "created"


def run(api: Github, pr_number: int, *, refresh: bool = False) -> str:
    if not 1 <= pr_number <= 1_000_000_000:
        raise PublishError("invalid PR number")
    pr = api.json("GET", api.prefix + f"/pulls/{pr_number}")
    if not isinstance(pr, dict) or pr.get("state") != "open":
        raise PublishError("PR is no longer open")
    head = pr.get("head") or {}
    owner = head.get("repo") or {}
    if owner.get("full_name") != api.repo:
        return "skipped_fork"
    head_sha = _sha(head.get("sha"))
    changed_files = pr.get("changed_files")
    if type(changed_files) is not int or not 0 <= changed_files <= MAX_CHANGED_FILES:
        raise PublishError("PR changed-file count exceeds bounded preview scope")
    changed = api.json("GET", api.prefix + f"/pulls/{pr_number}/files?per_page=100")
    if len(changed) != changed_files:
        raise PublishError("PR file listing incomplete; not publishing partial previews")
    if not isinstance(changed, list):
        raise PublishError("invalid PR files response")
    models = select_models(changed)
    if not models:
        return "no_model_changes"
    prior = _bot_comment(api, pr_number)
    if (
        not refresh
        and prior is not None
        and f"<!-- alice-3d-preview:head:{head_sha} -->" in prior.get("body", "")
    ):
        return "unchanged"
    return upsert_comment(api, pr_number, render_report(api, pr_number, head_sha, models))


def main() -> int:
    parser = argparse.ArgumentParser(description="Trusted GitHub PR 3D preview publisher")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument(
        "--refresh", action="store_true", help="rebuild previews even on unchanged PR head"
    )
    options = parser.parse_args()
    try:
        result = run(
            Github(options.repo, os.environ.get("GH_TOKEN", "")),
            options.pr,
            refresh=options.refresh,
        )
    except (PublishError, PreviewError) as exc:
        print("PREVIEW_BLOCKED:", str(exc)[:200], file=sys.stderr)
        return 2
    print("PREVIEW_RESULT:", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
