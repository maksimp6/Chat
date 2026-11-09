"""GitHub data changed since the last sync: issues and pull requests, comments, review comments, Actions runs.

Each listing is asked only for what changed after its cursor (``since`` / ``created``),
oldest first, so a sync appends new records and never re-reads old ones. An edited issue
or comment is appended again as a new record of its new version; the old record stays.
A token is used when there is one (the call's own, else GITHUB_TOKEN); without one the
public API is used. A rate limit stops the sync with the
cursor at the last stored record, and the next sync goes on from there.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from rag_sync.redact import mask

API = "https://api.github.com"
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class RateLimited(RuntimeError):
    """GitHub refused more requests now; the cursor stays where the data was stored."""


def repository() -> str:
    return (
        os.environ.get("GITHUB_REPOSITORY")
        or os.environ.get("LOGOS_COURT_REPO")
        or "maksimp6/Chat"
    )


def env_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or None


def _get(url: str, token: str | None) -> tuple[Any, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "logos-rag-sync",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=60
        ) as response:
            return json.loads(response.read().decode()), response.headers.get(
                "Link", ""
            )
    except urllib.error.HTTPError as error:
        if error.code == 429 or (
            error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
        ):
            raise RateLimited(f"GitHub rate limit (HTTP {error.code})") from error
        raise


def _pages(
    path: str, params: dict[str, str], token: str | None, key: str | None = None
) -> Iterator[dict]:
    url = API + path + "?" + urllib.parse.urlencode({**params, "per_page": "100"})
    while url:
        body, link = _get(url, token)
        rows = body.get(key, []) if key else body
        yield from rows
        match = _NEXT.search(link)
        url = match.group(1) if match else ""


KINDS = ("issues", "issue_comments", "review_comments", "runs")


def _listing(
    kind: str, repo: str, cursor: str | None, token: str | None
) -> Iterator[dict]:
    if kind == "runs":
        params = {"created": f">={cursor}"} if cursor else {}
        rows = list(
            _pages(f"/repos/{repo}/actions/runs", params, token, "workflow_runs")
        )
        yield from sorted(
            rows, key=lambda row: (row.get("created_at") or "", row.get("id") or 0)
        )
        return
    path = {
        "issues": f"/repos/{repo}/issues",
        "issue_comments": f"/repos/{repo}/issues/comments",
        "review_comments": f"/repos/{repo}/pulls/comments",
    }[kind]
    params = {"sort": "updated", "direction": "asc"}
    if kind == "issues":
        params["state"] = "all"
    if cursor:
        params["since"] = cursor
    yield from _pages(path, params, token)


def cursor_of(kind: str, row: dict) -> str:
    return str(row.get("created_at") if kind == "runs" else row.get("updated_at") or "")


def _record(kind: str, row: dict) -> str:
    if kind == "issues":
        what = "Pull request" if row.get("pull_request") else "Issue"
        labels = ", ".join(label.get("name", "") for label in row.get("labels") or [])
        head = [
            f"## {what} #{row.get('number')}: {row.get('title')}",
            f"State: {row.get('state')}"
            + (f" ({row.get('state_reason')})" if row.get("state_reason") else ""),
            f"Author: {(row.get('user') or {}).get('login')}",
            f"Created: {row.get('created_at')} · Updated: {row.get('updated_at')} · Closed: {row.get('closed_at')}",
            f"Labels: {labels}",
            f"URL: {row.get('html_url')}",
        ]
        return "\n".join(head) + "\n\n" + (row.get("body") or "")
    if kind == "issue_comments":
        number = str(row.get("issue_url") or "").rsplit("/", 1)[-1]
        return (
            f"## Comment on #{number} by {(row.get('user') or {}).get('login')}\n"
            f"Created: {row.get('created_at')} · Updated: {row.get('updated_at')}\nURL: {row.get('html_url')}\n\n"
            + (row.get("body") or "")
        )
    if kind == "review_comments":
        number = str(row.get("pull_request_url") or "").rsplit("/", 1)[-1]
        return (
            f"## Review comment on PR #{number} by {(row.get('user') or {}).get('login')}\n"
            f"File: {row.get('path')} · Line: {row.get('line') or row.get('original_line')}\n"
            f"Created: {row.get('created_at')} · Updated: {row.get('updated_at')}\nURL: {row.get('html_url')}\n\n"
            + (row.get("diff_hunk") or "")
            + "\n\n"
            + (row.get("body") or "")
        )
    return (
        f"## Actions run {row.get('id')}: {row.get('name')} #{row.get('run_number')}\n"
        f"Event: {row.get('event')} · Branch: {row.get('head_branch')} · Commit: {row.get('head_sha')}\n"
        f"Status: {row.get('status')} · Conclusion: {row.get('conclusion')}\n"
        f"Created: {row.get('created_at')} · Updated: {row.get('updated_at')}\nURL: {row.get('html_url')}\n"
        f"Title: {row.get('display_title')}"
    )


def records(
    kind: str, repo: str, cursor: str | None, edge: set[str], token: str | None
) -> Iterator[tuple[str, str, str, int]]:
    """(cursor value, record key, record text, masked count) of every item after ``cursor``.

    ``since`` is inclusive: items at exactly ``cursor`` whose key is in ``edge`` were
    stored by the previous sync and are skipped."""
    for row in _listing(kind, repo, cursor, token):
        at = cursor_of(kind, row)
        key = f"{row.get('id')}@{row.get('updated_at')}"
        if cursor and at == cursor and key in edge:
            continue
        text, masked = mask(_record(kind, row))
        yield at, key, text, masked
