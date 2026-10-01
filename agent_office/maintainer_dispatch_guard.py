"""Fail-closed idempotency guard for paid Maintainer dispatches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


API_ROOT = "https://api.github.com"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAINTAINER_INTENT = re.compile(r"\bmaintainer\b", re.I)
RETRY_INTENT = re.compile(r"\bretry\s*:", re.I)
CLAIM_RE = re.compile(r"<!-- alice-maintainer-dispatch-claim:(\{.*?\}) -->")
DUPLICATE_RE = re.compile(r"<!-- alice-maintainer-dispatch-duplicate:(\{.*?\}) -->")
MATERIAL_FAILURE = re.compile(
    r"(?:\bBLOCKED\s*:|Claude encountered an error|Claude execution failed|result is_error:true)",
    re.I,
)
CLAIM_BOT = "github-actions[bot]"


@dataclass(frozen=True)
class GuardDecision:
    run_model: bool
    reason: str
    idempotency_key: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    source_comment_id: int | None = None
    prior_run_id: int | None = None
    retry_of: int | None = None


def build_idempotency_key(repo: str, pr_number: int, head_sha: str) -> str:
    raw = f"maintainer:{repo}:{pr_number}:{head_sha}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _marker_payload(comment: dict[str, Any], pattern: re.Pattern[str]) -> dict[str, Any] | None:
    if ((comment.get("user") or {}).get("login") or "") != CLAIM_BOT:
        return None
    match = pattern.search(comment.get("body") or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _matching_claims(comments: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for comment in comments:
        payload = _marker_payload(comment, CLAIM_RE)
        if payload and payload.get("key") == key:
            claims.append(payload)
    return claims


def _material_failure_for_run(comments: list[dict[str, Any]], run_id: int) -> bool:
    run_fragment = f"/actions/runs/{run_id}"
    return any(
        ((comment.get("user") or {}).get("login") or "") == "claude[bot]"
        and run_fragment in (comment.get("body") or "")
        and bool(MATERIAL_FAILURE.search(comment.get("body") or ""))
        for comment in comments
    )


def evaluate_maintainer_dispatch(
    event: dict[str, Any],
    pr: dict[str, Any],
    comments: list[dict[str, Any]],
    *,
    repo: str,
    run_id: int,
) -> GuardDecision:
    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    body = comment.get("body") or ""

    if "pull_request" not in issue or not MAINTAINER_INTENT.search(body):
        return GuardDecision(True, "not_maintainer")

    association = str(comment.get("author_association") or "").upper()
    if association not in TRUSTED_ASSOCIATIONS:
        return GuardDecision(False, "untrusted_author")

    pr_number = int(issue["number"])
    head_sha = str(((pr.get("head") or {}).get("sha")) or "")
    if not head_sha:
        raise ValueError("pull request head SHA is required")

    source_comment_id = int(comment["id"])
    key = build_idempotency_key(repo, pr_number, head_sha)
    claims = _matching_claims(comments, key)

    if not claims:
        return GuardDecision(
            True,
            "claimed",
            key,
            pr_number,
            head_sha,
            source_comment_id,
        )

    prior = claims[-1]
    prior_run_id = int(prior["run_id"])
    if int(prior.get("source_comment_id") or 0) == source_comment_id:
        return GuardDecision(
            False,
            "duplicate",
            key,
            pr_number,
            head_sha,
            source_comment_id,
            prior_run_id,
        )

    if not RETRY_INTENT.search(body):
        return GuardDecision(
            False,
            "duplicate",
            key,
            pr_number,
            head_sha,
            source_comment_id,
            prior_run_id,
        )

    if not _material_failure_for_run(comments, prior_run_id):
        return GuardDecision(
            False,
            "retry_not_eligible",
            key,
            pr_number,
            head_sha,
            source_comment_id,
            prior_run_id,
        )

    return GuardDecision(
        True,
        "retry_claimed",
        key,
        pr_number,
        head_sha,
        source_comment_id,
        prior_run_id,
        prior_run_id,
    )


def claim_comment_body(decision: GuardDecision, run_id: int) -> str:
    payload = {
        "key": decision.idempotency_key,
        "source_comment_id": decision.source_comment_id,
        "run_id": run_id,
        "head_sha": decision.head_sha,
        "retry_of": decision.retry_of,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        f"<!-- alice-maintainer-dispatch-claim:{encoded} -->\n"
        f"Maintainer dispatch claimed for exact head `{decision.head_sha}` "
        f"(run `{run_id}`)."
    )


def duplicate_comment_body(decision: GuardDecision, run_id: int) -> str:
    payload = {
        "key": decision.idempotency_key,
        "source_comment_id": decision.source_comment_id,
        "run_id": run_id,
        "prior_run_id": decision.prior_run_id,
        "reason": decision.reason,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        f"<!-- alice-maintainer-dispatch-duplicate:{encoded} -->\n"
        "Maintainer handoff deduplicated before model execution. "
        f"Reason: `{decision.reason}`; authoritative run: "
        f"`{decision.prior_run_id or 'none'}`."
    )


class GitHub:
    def __init__(self, token: str, repo: str, opener: Callable[..., Any] | None = None):
        self.token = token
        self.repo = repo
        self._open = opener or urllib.request.urlopen

    def request(self, method: str, path: str, payload: Any = None) -> tuple[Any, dict[str, str]]:
        url = path if path.startswith("https://") else f"{API_ROOT}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "alice-maintainer-dispatch-guard")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with self._open(req, timeout=30) as response:
            body = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
        return (json.loads(body) if body else None), headers

    def get(self, path: str) -> Any:
        return self.request("GET", path)[0]

    def post(self, path: str, payload: Any) -> Any:
        return self.request("POST", path, payload)[0]

    def paginate(self, path: str, limit: int = 500) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url: str | None = path
        while url and len(items) < limit:
            page, headers = self.request("GET", url)
            items.extend(page or [])
            url = _next_link(headers.get("link", ""))
        return items[:limit]

    def repo_path(self, suffix: str) -> str:
        return f"/repos/{self.repo}{suffix}"


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        section = part.split(";")
        if len(section) > 1 and 'rel="next"' in section[1]:
            return section[0].strip()[1:-1]
    return None


def _write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _write_summary(decision: GuardDecision) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("## Maintainer dispatch idempotency\n\n")
        handle.write("~~~json\n")
        json.dump(asdict(decision), handle, ensure_ascii=False, indent=2)
        handle.write("\n~~~\n")


def run_guard(
    gh: GitHub,
    event: dict[str, Any],
    *,
    run_id: int,
) -> GuardDecision:
    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    body = comment.get("body") or ""
    if "pull_request" not in issue or not MAINTAINER_INTENT.search(body):
        return GuardDecision(True, "not_maintainer")

    number = int(issue["number"])
    pr = gh.get(gh.repo_path(f"/pulls/{number}"))
    comments = gh.paginate(gh.repo_path(f"/issues/{number}/comments?per_page=100"))
    decision = evaluate_maintainer_dispatch(event, pr, comments, repo=gh.repo, run_id=run_id)

    if decision.run_model and decision.reason in {"claimed", "retry_claimed"}:
        gh.post(
            gh.repo_path(f"/issues/{number}/comments"),
            {"body": claim_comment_body(decision, run_id)},
        )
    elif not decision.run_model and decision.reason in {"duplicate", "retry_not_eligible"}:
        already_recorded = any(
            (payload := _marker_payload(item, DUPLICATE_RE))
            and int(payload.get("run_id") or 0) == run_id
            for item in comments
        )
        if not already_recorded:
            gh.post(
                gh.repo_path(f"/issues/{number}/comments"),
                {"body": duplicate_comment_body(decision, run_id)},
            )
    return decision


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    token = os.environ.get("GITHUB_TOKEN") or ""
    run_id = int(os.environ.get("GITHUB_RUN_ID") or "0")
    if not event_path or not repo or not run_id:
        raise SystemExit("GITHUB_EVENT_PATH, GITHUB_REPOSITORY and GITHUB_RUN_ID are required")

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    decision = run_guard(GitHub(token, repo), event, run_id=run_id)
    _write_output("run_model", "true" if decision.run_model else "false")
    _write_output("reason", decision.reason)
    _write_output("idempotency_key", decision.idempotency_key)
    _write_output("head_sha", decision.head_sha)
    _write_summary(decision)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
