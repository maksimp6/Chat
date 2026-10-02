#!/usr/bin/env python3
"""Hosted CLI for the private Claude dialogue checkpoint protocol.

Only ciphertext paths and sanitized status go to stdout. Provider output, native
history and credentials stay private; failures never print external response data.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_office.claude_ci import (
    SESSION_LABEL,
    eligible_dialogue,
    encode_marker,
    finalize_dialogue,
    prepare_dialogue,
)
from agent_office.claude_runner import PersistentClaudeRunner


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise ValueError("github_configuration_invalid")
        self.base = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _json(self, method, suffix, **kwargs):
        try:
            response = requests.request(
                method,
                self.base + suffix,
                headers=self.headers,
                timeout=30,
                allow_redirects=False,
                **kwargs,
            )
            if response.status_code not in {200, 201} or len(response.content) > 16 * 1024 * 1024:
                raise ValueError("github_request_failed")
            return response.json()
        except (requests.RequestException, ValueError):
            raise ValueError("github_request_failed") from None

    def list_comments(self, issue):
        comments = []
        for page in range(1, 21):
            chunk = self._json(
                "GET", f"/issues/{issue}/comments", params={"per_page": 100, "page": page}
            )
            comments.extend(chunk)
            if len(chunk) < 100:
                return comments
        raise ValueError("github_queue_too_large")

    def get_issue(self, issue):
        return self._json("GET", f"/issues/{issue}")

    def write_permission(self, login):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            return False
        return self._json("GET", f"/collaborators/{login}/permission").get("permission") in {
            "write",
            "maintain",
            "admin",
        }

    def get_run(self, run):
        return self._json("GET", f"/actions/runs/{run}")

    def get_artifact(self, artifact):
        return self._json("GET", f"/actions/artifacts/{artifact}")

    def download_checkpoint(self, artifact):
        try:
            response = requests.get(
                self.base + f"/actions/artifacts/{artifact}/zip",
                headers=self.headers,
                timeout=30,
                allow_redirects=False,
            )
            location = response.headers.get("Location", "")
            parsed = urlparse(location)
            if (
                response.status_code != 302
                or parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.hostname.endswith(
                    (".githubusercontent.com", ".blob.core.windows.net", ".amazonaws.com")
                )
            ):
                raise ValueError("checkpoint_artifact_invalid")
            # Signed storage URLs must never receive the repository token.
            downloaded = requests.get(location, timeout=30, allow_redirects=False)
            if downloaded.status_code != 200 or len(downloaded.content) > 17 * 1024 * 1024:
                raise ValueError("checkpoint_artifact_invalid")
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                files = archive.infolist()
                if (
                    len(files) != 1
                    or files[0].filename != "checkpoint.bin"
                    or files[0].file_size > 17 * 1024 * 1024
                    or (files[0].external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("checkpoint_artifact_invalid")
                return archive.read(files[0])
        except (requests.RequestException, ValueError, zipfile.BadZipFile, OSError):
            raise ValueError("checkpoint_artifact_invalid") from None

    def post_marker(self, issue, marker):
        return self._json(
            "POST", f"/issues/{issue}/comments", json={"body": encode_marker(marker)}
        )["id"]

    def post_answer(self, issue, text):
        self._json("POST", f"/issues/{issue}/comments", json={"body": text})

    def add_session_label(self, issue):
        # Label creation is reversible issue metadata, never a permission grant.
        labels = self._json("GET", "/labels", params={"per_page": 100})
        if not any(label["name"] == SESSION_LABEL for label in labels):
            self._json(
                "POST",
                "/labels",
                json={
                    "name": SESSION_LABEL,
                    "color": "8b949e",
                    "description": "Private resumable Claude dialogue routing",
                },
            )
        self._json("POST", f"/issues/{issue}/labels", json={"labels": [SESSION_LABEL]})


def _outputs(values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            for key, value in values.items():
                if "\n" in str(value) or "\r" in str(value):
                    raise ValueError("ci_output_invalid")
                stream.write(f"{key}={value}\n")


def _preflight():
    version = subprocess.run(
        ["claude", "--version"], text=True, capture_output=True, timeout=15, check=False
    )
    help_text = subprocess.run(
        ["claude", "--help"], text=True, capture_output=True, timeout=15, check=False
    )
    required = (
        "--safe-mode",
        "--restricted",
        "--tools",
        "--strict-mcp-config",
        "--session-id",
        "--resume",
        "--system-prompt-snapshot",
        "--permission-prompts",
    )
    if (
        version.returncode
        or not version.stdout.startswith("2.1.283 ")
        or help_text.returncode
        or not all(flag in help_text.stdout for flag in required)
    ):
        raise ValueError("claude_cli_unsupported")
    auth = subprocess.run(
        ["claude", "--safe-mode", "--restricted", "auth", "status", "--json"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if auth.returncode or json.loads(auth.stdout).get("loggedIn") is not True:
        raise ValueError("claude_auth_missing")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("check", "prepare", "finalize"))
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--artifact-id", type=int)
    args = parser.parse_args(argv)
    try:
        github = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        if args.phase == "check":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            _outputs(
                {
                    "eligible": "true"
                    if eligible_dialogue(github, event["issue"]["number"])
                    else "false"
                }
            )
            print("Dialogue routing checked; no model call.")
            return 0
        if args.phase == "finalize":
            finalize_dialogue(
                github, json.loads(args.metadata.read_text()), artifact_id=args.artifact_id
            )
            print("Checkpoint committed; dialogue sleeping.")
            return 0
        secret = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not secret:
            raise ValueError("claude_auth_missing")
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop("ANTHROPIC_API_KEY", None)
        _preflight()
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        private = args.metadata.parent
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        runner = PersistentClaudeRunner(
            state_dir=private / "state",
            workspace=Path(os.environ["GITHUB_WORKSPACE"]),
            claude_config_dir=private / "native",
        )
        metadata = prepare_dialogue(
            github,
            runner,
            repo_id=event["repository"]["id"],
            repository=os.environ["GITHUB_REPOSITORY"],
            issue_number=event["issue"]["number"],
            run_id=int(os.environ["GITHUB_RUN_ID"]),
            head_sha=os.environ["GITHUB_SHA"],
            secret=secret,
            output_dir=private / "opaque",
            billing_source="subscription_usage"
            if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            else "api_usage_estimate",
        )
        if metadata is None:
            _outputs({"ready": "false"})
            print("No authorized new command; no model call.")
            return 0
        args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        args.metadata.chmod(0o600)
        _outputs(
            {
                "ready": "true",
                "checkpoint_path": metadata["checkpoint_path"],
                "artifact_name": metadata["artifact_name"],
            }
        )
        print("Opaque checkpoint ready for upload.")
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        # Do not expose CLI/API error text or credential values in public logs.
        known = {
            "github_configuration_invalid",
            "github_request_failed",
            "github_queue_too_large",
            "checkpoint_artifact_invalid",
            "checkpoint_anchor_invalid",
            "checkpoint_incomplete",
            "checkpoint_invalid",
            "checkpoint_missing",
            "checkpoint_session_blocked",
            "checkpoint_queue_unauthorized",
            "checkpoint_provider_blocked",
            "ci_output_invalid",
            "claude_cli_unsupported",
            "claude_auth_missing",
        }
        reason = str(error) if str(error) in known else "session_unavailable"
        print(f"BLOCKED: {reason}; no automatic replay.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
