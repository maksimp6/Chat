#!/usr/bin/env python3
"""Publish one runner capture to Git LFS without touching the application checkout."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = "maksimp6/Chat"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
LFS_VERSION = b"version https://git-lfs.github.com/spec/v1\n"
MAX_CAPTURE_PNGS = 24
EXTRA_ARTIFACT_LIMITS = {"15-conversation-create.mp4": 20 * 1024 * 1024,
                         "16-dialogs-walk.mp4": 24 * 1024 * 1024,
                         "events.json": 1024 * 1024}


class PublicationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise PublicationError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def is_lfs_file(name):
    return name.endswith((".png", ".mp4"))


def validate_mp4(path):
    """Require a finalized container; an ftyp-only recorder output is insufficient."""
    size = path.stat().st_size
    seen = set()
    offset = 0
    with path.open("rb") as stream:
        while offset < size:
            require(size - offset >= 8, "truncated MP4 box header")
            stream.seek(offset)
            header = stream.read(8)
            length, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if length == 1:
                require(size - offset >= 16, "truncated MP4 large box header")
                length = int.from_bytes(stream.read(8), "big")
                header_size = 16
            elif length == 0:
                length = size - offset
            require(header_size <= length <= size - offset, "invalid MP4 box size")
            if kind in (b"ftyp", b"moov", b"mdat"):
                require(length > header_size, "empty required MP4 box")
                seen.add(kind)
            offset += length
    require({b"ftyp", b"moov", b"mdat"} <= seen, "video container was not finalized")


def artifact_entries(data):
    entries = data.get("artifacts", [])
    require(isinstance(entries, list) and len(entries) <= len(EXTRA_ARTIFACT_LIMITS),
            "unexpected supplementary capture artifacts")
    seen = set()
    for entry in entries:
        require(isinstance(entry, dict), "invalid supplementary capture entry")
        name, sha, size = entry.get("path"), entry.get("sha256"), entry.get("size")
        require(name in EXTRA_ARTIFACT_LIMITS and name not in seen, "invalid supplementary filename")
        require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha), "invalid artifact hash")
        require(type(size) is int and 0 < size <= EXTRA_ARTIFACT_LIMITS[name], "invalid artifact size")
        seen.add(name)
    return entries


def capture_files(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = directory / "manifest.json"
    require(manifest.is_file() and not manifest.is_symlink(), "invalid capture manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("files")
    require(isinstance(entries, list) and 3 <= len(entries) <= MAX_CAPTURE_PNGS,
            "expected between three and twenty capture PNGs")
    files = {}
    for entry in entries:
        require(isinstance(entry, dict), "invalid capture entry")
        name = entry.get("path")
        require(isinstance(name, str) and re.fullmatch(r"[0-9]{2}-[a-z0-9-]+\.png", name),
                "invalid capture filename")
        require(name not in files, "duplicate capture filename")
        path = directory / name
        require(path.is_file() and not path.is_symlink(), "capture PNG is missing")
        sha, size = entry.get("sha256"), entry.get("size")
        require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha), "invalid capture hash")
        require(type(size) is int and 0 < size <= 5 * 1024 * 1024 and path.stat().st_size == size,
                "capture size mismatch")
        with path.open("rb") as stream:
            require(stream.read(8) == b"\x89PNG\r\n\x1a\n", "capture is not a PNG")
        require(digest(path) == sha, "capture hash mismatch")
        files[name] = {"path": path, "sha256": sha, "size": size}
    for entry in artifact_entries(data):
        name, sha, size = entry["path"], entry["sha256"], entry["size"]
        path = directory / name
        require(path.is_file() and not path.is_symlink(), "supplementary capture is missing")
        require(path.stat().st_size == size and digest(path) == sha, "supplementary capture changed")
        if name.endswith(".mp4"):
            validate_mp4(path)
        else:
            json.loads(path.read_text(encoding="utf-8"))
        files[name] = {"path": path, "sha256": sha, "size": size}
    files["manifest.json"] = {"path": manifest, "sha256": digest(manifest),
                              "size": manifest.stat().st_size}
    return files


def runner_environment():
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true",
            "publication requires a GitHub Actions job")
    for name in ("RUNNER_NAME", "RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_TOKEN"):
        require(bool(os.environ.get(name)), "required runner context is missing")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "unexpected job repository")
    publication_ref(os.environ)
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(re.fullmatch(r"[1-9][0-9]*", run_id) and re.fullmatch(r"[1-9][0-9]*", attempt),
            "invalid runner identity")
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("GIT_") or name == "GIT_CONFIG":
            del env[name]
    for name in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_DIR", "GIT_WORK_TREE"):
        env.pop(name, None)
    auth = base64.b64encode(("x-access-token:" + env["GITHUB_TOKEN"]).encode()).decode()
    env.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_LFS_SKIP_SMUDGE": "1",
                "GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth,
                "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
                "GIT_CONFIG_KEY_2": "core.askPass", "GIT_CONFIG_VALUE_2": ""})
    return env, run_id, attempt


def publication_ref(env):
    ref = env.get("GITHUB_REF", "")
    require(re.fullmatch(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*", ref)
            and ".." not in ref and "//" not in ref and not ref.endswith(("/", ".", ".lock"))
            and ref != "refs/heads/master", "publication requires a non-master repository branch")
    return ref


def git(repo, env, *arguments, allow_failure=False):
    result = subprocess.run(["git", *arguments], cwd=repo, env=env, capture_output=True,
                            timeout=180, check=False)
    if result.returncode and not allow_failure:
        # Git/LFS diagnostics can contain authorization headers or signed URLs.
        raise PublicationError("git operation failed: " + arguments[0])
    return result


def configure(repo, env):
    git(repo, env, "init", "--quiet")
    git(repo, env, "remote", "add", "origin", REMOTE)
    settings = {"core.sparseCheckout": "true", "core.sparseCheckoutCone": "false",
                "remote.origin.promisor": "true", "remote.origin.partialclonefilter": "blob:none",
                "user.name": "github-actions[bot]",
                "user.email": "41898282+github-actions[bot]@users.noreply.github.com",
                "filter.lfs.clean": "git-lfs clean -- %f",
                "filter.lfs.smudge": "git-lfs smudge --skip -- %f",
                "filter.lfs.process": "git-lfs filter-process", "filter.lfs.required": "true",
                "lfs.url": REMOTE + "/info/lfs", "remote.origin.lfsurl": REMOTE + "/info/lfs",
                "remote.origin.lfspushurl": REMOTE + "/info/lfs"}
    for name, value in settings.items():
        git(repo, env, "config", "--local", name, value)
    (repo / ".git/info/sparse-checkout").write_text(
        "/.gitattributes\n/docs/screenshots/logos/\n", encoding="utf-8")


def pointer(metadata):
    return LFS_VERSION + ("oid sha256:" + metadata["sha256"] + "\nsize "
                          + str(metadata["size"]) + "\n").encode()


def validate_git_blob(blob, name, metadata):
    if is_lfs_file(name):
        require(blob == pointer(metadata), "file is not the expected Git LFS pointer")
    else:
        require(len(blob) == metadata["size"] and hashlib.sha256(blob).hexdigest() == metadata["sha256"],
                "manifest does not match the ordinary Git blob")


def publish(repo, env, files, relative, run_id, attempt):
    target_ref = publication_ref(env)
    for retry in range(3):
        git(repo, env, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", target_ref)
        git(repo, env, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        destination = repo / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name, metadata in files.items():
            target = destination / name
            require(not target.exists(), "run output already exists on the publication branch")
            shutil.copyfile(metadata["path"], target)
            require(target.stat().st_size == metadata["size"] and digest(target) == metadata["sha256"],
                    "capture changed while copying")
        git(repo, env, "add", "--", relative)
        for name, metadata in files.items():
            staged = git(repo, env, "show", ":" + relative + "/" + name).stdout
            validate_git_blob(staged, name, metadata)
        git(repo, env, "commit", "--quiet", "-m",
            "docs(evidence): Redmi 9 screenshots, run " + run_id + " attempt " + attempt)
        commit = git(repo, env, "rev-parse", "HEAD").stdout.decode().strip()
        oids = [metadata["sha256"] for name, metadata in files.items() if is_lfs_file(name)]
        git(repo, env, "lfs", "push", "--object-id", "origin", *oids)
        pushed = git(repo, env, "push", "--porcelain", "origin", "HEAD:" + target_ref, allow_failure=True)
        if pushed.returncode == 0:
            return commit
        diagnostic = (pushed.stdout + pushed.stderr).decode(errors="replace").lower()
        non_fast_forward = "(non-fast-forward)" in diagnostic or "(fetch first)" in diagnostic
        require(non_fast_forward and retry < 2, "branch push failed; no force push was attempted")
    raise PublicationError("publication branch advanced during publication")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        old, new = urlsplit(request.full_url), urlsplit(target)
        require(new.scheme == "https" and not new.username and not new.password,
                "unsafe LFS download redirect")
        redirected = super().redirect_request(request, response, code, message, headers, target)
        if redirected is not None and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            redirected.remove_header("Authorization")
            redirected.remove_header("Cookie")
        return redirected


def download_objects(env, files, storage):
    # Git LFS Batch + Basic APIs: known OIDs only; no ls-tree over a promisor repo.
    # https://github.com/git-lfs/git-lfs/blob/main/docs/api/batch.md
    # https://github.com/git-lfs/git-lfs/blob/main/docs/api/basic-transfers.md
    wanted = {item["sha256"]: item for name, item in files.items() if is_lfs_file(name)}
    body = {"operation": "download", "transfers": ["basic"],
            "ref": {"name": publication_ref(env)},
            "objects": [{"oid": sha, "size": item["size"]} for sha, item in wanted.items()]}
    authorization = env["GIT_CONFIG_VALUE_0"].split(":", 1)[1].strip()
    opener = build_opener(SafeRedirect())
    request = Request(REMOTE + "/info/lfs/objects/batch", data=json.dumps(body).encode(),
                      headers={"Accept": "application/vnd.git-lfs+json",
                               "Content-Type": "application/vnd.git-lfs+json",
                               "Authorization": authorization}, method="POST")
    with opener.open(request, timeout=30) as response:
        batch = json.loads(response.read(1024 * 1024))
    require(batch.get("transfer", "basic") == "basic", "unsupported LFS transfer")
    objects = batch.get("objects", [])
    require(isinstance(objects, list) and len(objects) == len(wanted), "unexpected LFS object count")
    seen = set()
    storage.mkdir(parents=True)
    for item in objects:
        sha = item.get("oid")
        require(sha in wanted and sha not in seen and not item.get("error"), "LFS object unavailable")
        seen.add(sha)
        metadata = wanted[sha]
        require(item.get("size") == metadata["size"], "LFS size differs from capture")
        action = item.get("actions", {}).get("download", {})
        url = action.get("href", "")
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password,
                "unsafe LFS download action")
        download_headers = dict(action.get("header", {}))
        # The job token is sent only to the fixed GitHub batch endpoint, never copied to storage.
        download = Request(url, headers=download_headers, method="GET")
        with opener.open(download, timeout=30) as response:
            content = response.read(metadata["size"] + 1)
        require(len(content) == metadata["size"] and hashlib.sha256(content).hexdigest() == sha,
                "remote Git LFS bytes differ from the original capture")
        downloaded = storage / sha
        downloaded.write_bytes(content)
        if metadata["path"].suffix == ".mp4":
            validate_mp4(downloaded)
    require(seen == set(wanted), "missing LFS download")


def verify(repo, env, files, relative, storage):
    download_objects(env, files, storage)
    git(repo, env, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", publication_ref(env))
    for name, metadata in files.items():
        remote_pointer = git(repo, env, "show", "FETCH_HEAD:" + relative + "/" + name).stdout
        validate_git_blob(remote_pointer, name, metadata)


def latest_capture(repo, env):
    base = repo / "docs/screenshots/logos"
    require(base.is_dir(), "no published screenshot captures")
    require(not any(path.is_symlink() for path in (repo / "docs", repo / "docs/screenshots", base)),
            "unsafe screenshot directory")
    candidates = []
    for manifest in base.glob("*/*/manifest.json"):
        day, folder = manifest.parent.parent.name, manifest.parent.name
        match = re.fullmatch(r"run-([1-9][0-9]*)-([1-9][0-9]*)", folder)
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day) or not match:
            continue
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        require(not any(path.is_symlink() for path in (manifest, manifest.parent, manifest.parent.parent)),
                "unsafe capture manifest path")
        candidates.append((int(match[1]), int(match[2]), day, manifest))
    require(bool(candidates), "no published screenshot manifests")
    run_id, attempt, _, manifest = max(candidates, key=lambda item: item[:3])
    relative = manifest.parent.relative_to(repo).as_posix()
    raw = git(repo, env, "show", "HEAD:" + relative + "/manifest.json").stdout
    require(not raw.startswith(LFS_VERSION) and manifest.read_bytes() == raw,
            "capture manifest must be an ordinary Git blob")
    data = json.loads(raw)
    require(isinstance(data, dict) and str(data.get("github_run_id")) == str(run_id)
            and str(data.get("github_run_attempt")) == str(attempt),
            "capture manifest run identity differs from its folder")
    entries = data.get("files")
    require(isinstance(entries, list) and 3 <= len(entries) <= MAX_CAPTURE_PNGS,
            "expected between three and twenty published capture PNGs")
    files = {}
    for entry in entries:
        require(isinstance(entry, dict), "invalid published capture entry")
        name, sha, size = entry.get("path"), entry.get("sha256"), entry.get("size")
        require(isinstance(name, str) and re.fullmatch(r"[0-9]{2}-[a-z0-9-]+\.png", name)
                and name not in files, "invalid published capture filename")
        require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha), "invalid published capture hash")
        require(type(size) is int and 0 < size <= 5 * 1024 * 1024, "invalid published capture size")
        path = manifest.parent / name
        require(path.is_file() and not path.is_symlink(), "published capture pointer is missing")
        metadata = {"path": path, "sha256": sha, "size": size}
        blob = git(repo, env, "show", "HEAD:" + relative + "/" + name).stdout
        validate_git_blob(blob, name, metadata)
        require(path.read_bytes() == blob, "capture pointer differs from its Git blob")
        files[name] = metadata
    for entry in artifact_entries(data):
        name, sha, size = entry["path"], entry["sha256"], entry["size"]
        path = manifest.parent / name
        require(path.is_file() and not path.is_symlink(), "published supplementary capture is missing")
        metadata = {"path": path, "sha256": sha, "size": size}
        blob = git(repo, env, "show", "HEAD:" + relative + "/" + name).stdout
        validate_git_blob(blob, name, metadata)
        require(path.read_bytes() == blob, "supplementary capture differs from its Git blob")
        files[name] = metadata
    files["manifest.json"] = {"path": manifest, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return files, relative, str(run_id), str(attempt)


def verify_latest():
    env, run_id, attempt = runner_environment()
    with tempfile.TemporaryDirectory(prefix="logos-lfs-verify-", dir=env["RUNNER_TEMP"]) as temporary:
        root = Path(temporary)
        repo = root / "repository"
        repo.mkdir()
        configure(repo, env)
        git(repo, env, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", publication_ref(env))
        git(repo, env, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        files, relative, capture_run, capture_attempt = latest_capture(repo, env)
        verify(repo, env, files, relative, root / "remote-lfs-verification")
        commit = git(repo, env, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
        result = {"verification_run_id": run_id, "verification_run_attempt": attempt,
                  "capture_run_id": capture_run, "capture_run_attempt": capture_attempt,
                  "commit": commit, "ref": publication_ref(env), "verified": True,
                  "files": [{"path": relative + "/" + name, "sha256": metadata["sha256"],
                             "size": metadata["size"]} for name, metadata in files.items()]}
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("### Existing Redmi 9 screenshots verified in Git LFS\n\n")
                for item in result["files"]:
                    url = "https://github.com/" + REPOSITORY + "/blob/" + commit + "/" + item["path"]
                    stream.write("- [" + Path(item["path"]).name + "](" + url + ") · SHA256 `"
                                 + item["sha256"] + "`\n")
        print(json.dumps(result, ensure_ascii=False))


def main():
    if sys.argv[1:] == ["--verify-latest"]:
        verify_latest()
        return
    require(len(sys.argv) == 2, "usage: publish_redmi_screenshots.py CAPTURE_DIR")
    env, run_id, attempt = runner_environment()
    files = capture_files(sys.argv[1])
    day = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    relative = "docs/screenshots/logos/" + day + "/run-" + run_id + "-" + attempt
    with tempfile.TemporaryDirectory(prefix="logos-lfs-", dir=env["RUNNER_TEMP"]) as temporary:
        root = Path(temporary)
        repo = root / "repository"
        repo.mkdir()
        configure(repo, env)
        commit = publish(repo, env, files, relative, run_id, attempt)
        print(json.dumps({"published_commit": commit, "verification": "pending"}), flush=True)
        verify(repo, env, files, relative, root / "remote-lfs-verification")
        result = {"commit": commit, "ref": publication_ref(env), "run_id": run_id, "run_attempt": attempt,
                  "files": [{"path": relative + "/" + name, "sha256": metadata["sha256"],
                             "size": metadata["size"]} for name, metadata in files.items()]}
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("### Redmi 9 screenshots verified in Git LFS\n\n")
                for item in result["files"]:
                    url = "https://github.com/" + REPOSITORY + "/blob/" + commit + "/" + item["path"]
                    stream.write("- [" + Path(item["path"]).name + "](" + url + ") · SHA256 `"
                                 + item["sha256"] + "`\n")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except PublicationError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('{"error":"screenshot publication failed; diagnostics withheld"}', file=sys.stderr)
        sys.exit(1)
