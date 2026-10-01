#!/usr/bin/env python3
"""Fail-closed idempotency guard for keyed Claude-Lite issue-comment dispatches."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
_MARKER_PREFIX = "agent-dispatch:maintainer:"
_MARKER = re.compile(
    r"<!--\s*agent-dispatch:maintainer:([0-9a-f]{40})(?::([A-Za-z0-9._-]{1,40}))?\s*-->",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DispatchMarker:
    head_sha: str
    attempt: str | None

    @property
    def key(self) -> str:
        suffix = f":{self.attempt}" if self.attempt else ""
        return f"maintainer:{self.head_sha}{suffix}"


@dataclass(frozen=True)
class DispatchDecision:
    should_run: bool
    reason: str
    dispatch_key: str = ""


def parse_dispatch_marker(body: str | None) -> DispatchMarker | None:
    text = body or ""
    if _MARKER_PREFIX not in text.lower():
        return None
    matches = list(_MARKER.finditer(text))
    if len(matches) != 1:
        raise ValueError("maintainer dispatch marker must appear exactly once and be valid")
    head_sha, attempt = matches[0].groups()
    return DispatchMarker(head_sha=head_sha.lower(), attempt=attempt)


def decide_dispatch(
    *,
    current_comment_id: int,
    current_author_association: str,
    body: str,
    pr_head_sha: str,
    comments: list[dict[str, Any]],
) -> DispatchDecision:
    try:
        marker = parse_dispatch_marker(body)
    except ValueError:
        return DispatchDecision(False, "invalid_dispatch_marker")

    if marker is None:
        return DispatchDecision(True, "unkeyed_dispatch")

    if current_author_association.upper() not in TRUSTED_ASSOCIATIONS:
        return DispatchDecision(False, "untrusted_dispatch_author", marker.key)

    if pr_head_sha.lower() != marker.head_sha:
        return DispatchDecision(False, "stale_dispatch_head", marker.key)

    matching_ids: list[int] = []
    for comment in comments:
        if str(comment.get("author_association") or "").upper() not in TRUSTED_ASSOCIATIONS:
            continue
        try:
            candidate = parse_dispatch_marker(comment.get("body"))
        except ValueError:
            continue
        if candidate is not None and candidate.key == marker.key:
            matching_ids.append(int(comment["id"]))

    if not matching_ids:
        return DispatchDecision(False, "dispatch_key_not_found", marker.key)

    first_comment_id = min(matching_ids)
    if current_comment_id != first_comment_id:
        return DispatchDecision(False, f"duplicate_of_comment_{first_comment_id}", marker.key)

    return DispatchDecision(True, "primary_dispatch", marker.key)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _gh_json(args: list[str], runner: Runner = subprocess.run) -> Any:
    completed = runner(
        ["gh", "api", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def fetch_pr_head(repo: str, number: int, runner: Runner = subprocess.run) -> str:
    payload = _gh_json([f"repos/{repo}/pulls/{number}"], runner)
    return str(payload["head"]["sha"])


def fetch_issue_comments(
    repo: str,
    number: int,
    runner: Runner = subprocess.run,
    *,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        batch = _gh_json(
            [f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}"],
            runner,
        )
        if not isinstance(batch, list):
            raise ValueError("GitHub comments response must be a list")
        comments.extend(batch)
        if len(batch) < 100:
            return comments
    raise RuntimeError("comment pagination limit exceeded")


def write_outputs(decision: DispatchDecision, output_path: str) -> None:
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"should_run={'true' if decision.should_run else 'false'}\n")
        handle.write(f"reason={decision.reason}\n")
        handle.write(f"dispatch_key={decision.dispatch_key}\n")


def main() -> int:
    body = os.environ.get("DISPATCH_BODY", "")
    output_path = os.environ.get("GITHUB_OUTPUT", "")

    try:
        marker = parse_dispatch_marker(body)
        if marker is None:
            decision = DispatchDecision(True, "unkeyed_dispatch")
        else:
            repo = os.environ["DISPATCH_REPO"]
            number = int(os.environ["DISPATCH_ISSUE_NUMBER"])
            comment_id = int(os.environ["DISPATCH_COMMENT_ID"])
            association = os.environ.get("DISPATCH_AUTHOR_ASSOCIATION", "")
            decision = decide_dispatch(
                current_comment_id=comment_id,
                current_author_association=association,
                body=body,
                pr_head_sha=fetch_pr_head(repo, number),
                comments=fetch_issue_comments(repo, number),
            )
    except Exception as exc:
        decision = DispatchDecision(False, f"guard_error_{type(exc).__name__}")

    if output_path:
        write_outputs(decision, output_path)
    print(
        json.dumps(
            {
                "should_run": decision.should_run,
                "reason": decision.reason,
                "dispatch_key": decision.dispatch_key,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
