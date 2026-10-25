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


REPOSITORY = "maksimp6/Chat"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
LFS_VERSION = b"version https://git-lfs.github.com/spec/v1\n"


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


def capture_files(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = directory / "manifest.json"
    require(manifest.is_file() and not manifest.is_symlink(), "invalid capture manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("files")
    require(isinstance(entries, list) and len(entries) == 3, "expected three capture PNGs")
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
        require(type(size) is int and size > 0 and path.stat().st_size == size,
                "capture size mismatch")
        with path.open("rb") as stream:
            require(stream.read(8) == b"\x89PNG\r\n\x1a\n", "capture is not a PNG")
        require(digest(path) == sha, "capture hash mismatch")
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
    if name.endswith(".png"):
        require(blob == pointer(metadata), "file is not the expected Git LFS pointer")
    else:
        require(len(blob) == metadata["size"] and hashlib.sha256(blob).hexdigest() == metadata["sha256"],
                "manifest does not match the ordinary Git blob")


def publish(repo, env, files, relative, run_id, attempt):
    for retry in range(3):
        git(repo, env, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", "refs/heads/develop")
        git(repo, env, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        destination = repo / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name, metadata in files.items():
            target = destination / name
            require(not target.exists(), "run output already exists on develop")
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
        oids = [metadata["sha256"] for name, metadata in files.items() if name.endswith(".png")]
        git(repo, env, "lfs", "push", "--object-id", "origin", *oids)
        pushed = git(repo, env, "push", "--porcelain", "origin", "HEAD:refs/heads/develop", allow_failure=True)
        if pushed.returncode == 0:
            return commit
        diagnostic = (pushed.stdout + pushed.stderr).decode(errors="replace").lower()
        non_fast_forward = "(non-fast-forward)" in diagnostic or "(fetch first)" in diagnostic
        require(non_fast_forward and retry < 2, "develop push failed; no force push was attempted")
    raise PublicationError("develop advanced during publication")


def verify(repo, env, files, relative, storage):
    git(repo, env, "-c", "lfs.storage=" + str(storage), "-c", "lfs.fetchrecentalways=false",
        "lfs", "fetch", "--include=" + relative + "/**", "--exclude=", "origin", "HEAD")
    for name, metadata in files.items():
        if not name.endswith(".png"):
            continue
        sha = metadata["sha256"]
        downloaded = storage / "objects" / sha[:2] / sha[2:4] / sha
        require(downloaded.is_file() and downloaded.stat().st_size == metadata["size"]
                and digest(downloaded) == sha, "remote Git LFS object verification failed")
    git(repo, env, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", "refs/heads/develop")
    for name, metadata in files.items():
        remote_pointer = git(repo, env, "show", "FETCH_HEAD:" + relative + "/" + name).stdout
        validate_git_blob(remote_pointer, name, metadata)


def main():
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
        verify(repo, env, files, relative, root / "remote-lfs-verification")
        result = {"commit": commit, "run_id": run_id, "run_attempt": attempt,
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
