"""Trusted PR-head Memory DB admission, using *only* code from the target branch.

Never checkout, import, execute, or install anything from a candidate pull request.
The candidate is inspected as data through pinned-SHA GitHub Contents API reads.
A commit status is posted for the exact event head (not the base workflow SHA).
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


POLICY_FILE = Path(__file__).resolve().parents[1] / "ci" / "trusted_memory_policy.json"
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
API_URL = "https://api.github.com"
MAX_PROTECTED_FILE_BYTES = 1024 * 1024


class AdmissionError(RuntimeError):
    """A trusted-gate infrastructure or input validation failure."""


@dataclass(frozen=True)
class FileRecord:
    sha256: str
    blob_sha: str
    size: int


@dataclass(frozen=True)
class Decision:
    head_sha: str
    base_sha: str
    stage: str
    errors: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.errors


def is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None


def read_policy(path: Path = POLICY_FILE) -> dict[str, Any]:
    policy: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("policy_version") != 1 or policy.get("repository") != "maksimp6/Chat":
        raise AdmissionError("unsupported trusted policy version or repository")
    frozen = policy.get("pinned_sha256")
    if not isinstance(frozen, dict) or len(frozen) != 16:
        raise AdmissionError("trusted policy requires exactly 16 accepted file digests")
    if any(
        not isinstance(path, str)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
        for path, digest in frozen.items()
    ):
        raise AdmissionError("invalid trusted policy digests")
    if not is_sha(policy.get("approved_ci_git_blob_sha")):
        raise AdmissionError("invalid approved CI workflow blob SHA")
    return policy


class GitHubAPI:
    """Bounded GitHub REST access; never print tokens or HTTP authorization headers."""

    def __init__(self, token: str, repository: str) -> None:
        if not token:
            raise AdmissionError("trusted workflow token is missing")
        if repository != "maksimp6/Chat":
            raise AdmissionError("unexpected repository")
        self._token = token
        self.repository = repository
        self._file_cache: dict[tuple[str, str], FileRecord | None] = {}

    def _json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        allow_missing: bool = False,
    ) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload else None
        request = Request(
            API_URL + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "AlicePro-Trusted-Memory-Admission",
            },
        )
        try:
            with urlopen(request, timeout=12) as response:
                result = json.load(response)
                if not isinstance(result, dict):
                    raise AdmissionError("non-object GitHub API response")
                return result
        except HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            raise AdmissionError(
                f"GitHub API {method} rejected request (HTTP {exc.code})"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise AdmissionError(f"GitHub API {method} failed ({type(exc).__name__})") from None

    def get_pr(self, number: int) -> dict[str, Any]:
        if not 1 <= number <= 999999999:
            raise AdmissionError("invalid pull request number")
        result: dict[str, Any] = self._json("GET", f"/repos/{self.repository}/pulls/{number}")
        return result

    def file_at(self, sha: str, path: str) -> FileRecord | None:
        if not is_sha(sha) or not path or path.startswith("/") or ".." in path.split("/"):
            raise AdmissionError("invalid trusted file lookup")
        key = sha, path
        if key in self._file_cache:
            return self._file_cache[key]
        url_path = quote(path, safe="/")
        value = self._json(
            "GET",
            f"/repos/{self.repository}/contents/{url_path}?ref={sha}",
            allow_missing=True,
        )
        if value is None:
            self._file_cache[key] = None
            return None
        if value.get("type") != "file" or value.get("encoding") != "base64":
            raise AdmissionError(f"unsupported protected file encoding: {path}")
        try:
            encoded = value["content"].replace("\n", "")
            data = base64.b64decode(encoded, validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise AdmissionError(f"invalid protected file content: {path}") from None
        if len(data) > MAX_PROTECTED_FILE_BYTES or len(data) != value.get("size"):
            raise AdmissionError(f"unexpected protected file size: {path}")
        blob_sha = value.get("sha")
        if not is_sha(blob_sha):
            raise AdmissionError(f"invalid Git blob SHA: {path}")
        record = FileRecord(hashlib.sha256(data).hexdigest(), blob_sha, len(data))
        self._file_cache[key] = record
        return record

    def post_status(
        self, head_sha: str, state: str, context: str, description: str, target_url: str
    ) -> None:
        if not is_sha(head_sha) or state not in (
            "pending",
            "success",
            "failure",
            "error",
        ):
            raise AdmissionError("invalid commit status input")
        self._json(
            "POST",
            f"/repos/{self.repository}/statuses/{head_sha}",
            {
                "state": state,
                "context": context,
                "description": description[:140],
                "target_url": target_url,
            },
        )


def _sha_from_pr(pr: Mapping[str, Any], side: str) -> str | None:
    value = pr.get(side)
    if isinstance(value, dict):
        result = value.get("sha")
        return result if is_sha(result) else None
    return None


def _same_repository(pr: Mapping[str, Any], repo: str) -> bool:
    for side in ("head", "base"):
        value = pr.get(side)
        if not isinstance(value, dict) or not isinstance(value.get("repo"), dict):
            return False
        if value["repo"].get("full_name") != repo:
            return False
    return True


def _file_exists(api: Any, sha: str, path: str) -> bool:
    return api.file_at(sha, path) is not None


def _check_candidate_metadata(pr: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if pr.get("state") != "open":
        errors.append("pull request is not open")
    if not _same_repository(pr, str(policy["repository"])):
        errors.append("cross-repository candidate is not trusted")
    base_info = pr.get("base")
    if (
        not isinstance(base_info, dict)
        or base_info.get("ref") not in policy["protected_base_branches"]
    ):
        errors.append("PR does not target a trusted integration branch")
    return errors


def _check_trusted_files(api: Any, policy: Mapping[str, Any], head: str, base: str) -> list[str]:
    """Reject unsafe hooks and candidate edits to already-trusted admission controls."""
    errors: list[str] = []
    for path in policy["forbidden_paths"]:
        if _file_exists(api, head, path):
            errors.append(f"unapproved import hook: {path}")
    for path in policy["trusted_control_paths"]:
        in_base = api.file_at(base, path)
        if in_base is None:
            continue
        in_head = api.file_at(head, path)
        if in_head is None or in_head.sha256 != in_base.sha256:
            errors.append(f"trusted admission source was changed: {path}")
    return errors


def _determine_stage(
    api: Any, policy: Mapping[str, Any], head: str, base: str
) -> tuple[bool, bool]:
    core = any(_file_exists(api, sha, policy["core_marker"]) for sha in (base, head))
    fast = any(
        _file_exists(api, sha, path) for sha in (base, head) for path in policy["fast_markers"]
    )
    return core, fast


def _check_frozen_files(
    api: Any,
    policy: Mapping[str, Any],
    head: str,
    *,
    core_stage: bool,
    fast_stage: bool,
) -> list[str]:
    """Verify accepted tests; evolving core may change, but cannot disappear."""
    errors: list[str] = []
    frozen = policy["pinned_sha256"]
    required = set(policy["always_required"])
    if core_stage:
        required.update(policy["core_contracts"])
        if not _file_exists(api, head, policy["core_marker"]):
            errors.append(f"core implementation missing: {policy['core_marker']}")
    if fast_stage:
        required.update(frozen)
    for path in sorted(required):
        record = api.file_at(head, path)
        if record is None:
            errors.append(f"accepted file missing: {path}")
        elif record.sha256 != frozen[path]:
            errors.append(f"accepted file differs from pinned contract: {path}")
    if fast_stage:
        ci = api.file_at(head, ".github/workflows/ci.yml")
        if ci is None or ci.blob_sha != policy["approved_ci_git_blob_sha"]:
            errors.append("fast CI workflow differs from approved admission configuration")
    return errors


def _is_stale(api: Any, number: int, event_head: str, event_base: str) -> bool:
    current = api.get_pr(number)
    return (
        _sha_from_pr(current, "head") != event_head or _sha_from_pr(current, "base") != event_base
    )


def audit_pr(
    api: Any, policy: Mapping[str, Any], number: int, event_head: str, event_base: str
) -> Decision:
    """Check snapshot from current exact PR head and recheck for staleness."""
    if not is_sha(event_head) or not is_sha(event_base):
        raise AdmissionError("event must contain exact 40-character Git SHAs")
    pr = api.get_pr(number)
    if _sha_from_pr(pr, "head") != event_head or _sha_from_pr(pr, "base") != event_base:
        return Decision(
            event_head,
            event_base,
            "stale",
            ("PR event SHA does not match current head/base",),
        )
    core_stage, fast_stage = _determine_stage(api, policy, event_head, event_base)
    stage = "fast" if fast_stage else "core" if core_stage else "legacy"
    errors = _check_candidate_metadata(pr, policy)
    errors.extend(_check_trusted_files(api, policy, event_head, event_base))
    errors.extend(
        _check_frozen_files(api, policy, event_head, core_stage=core_stage, fast_stage=fast_stage)
    )
    if _is_stale(api, number, event_head, event_base):
        errors.append("PR advanced during trusted validation; stale evidence rejected")
    return Decision(event_head, event_base, stage, tuple(errors))


def execute(
    api: Any,
    policy: Mapping[str, Any],
    number: int,
    head: str,
    base: str,
    target_url: str,
) -> tuple[str, Decision | None]:
    """Always emit a terminal status for a head once its initial pending status exists."""
    context = str(policy["status_context"])
    api.post_status(
        head,
        "pending",
        context,
        "Trusted contract verification in progress",
        target_url,
    )
    decision: Decision | None = None
    try:
        decision = audit_pr(api, policy, number, head, base)
        state = "success" if decision.accepted else "failure"
        description = (
            f"Verified {decision.stage} contracts at exact head"
            if decision.accepted
            else decision.errors[0]
        )
    except (AdmissionError, KeyError, TypeError, ValueError) as exc:
        state = "error"
        description = f"Trusted validation error: {type(exc).__name__}"
    api.post_status(head, state, context, description, target_url)
    return state, decision


def main() -> int:
    """CLI used only by a trusted-base pull_request_target workflow."""
    try:
        policy = read_policy()
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        head = os.environ.get("PR_HEAD_SHA", "")
        base = os.environ.get("PR_BASE_SHA", "")
        number = int(os.environ.get("PR_NUMBER", ""))
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if repo != policy["repository"] or not run_id.isdecimal():
            raise AdmissionError("untrusted workflow environment")
        url = f"https://github.com/{repo}/actions/runs/{run_id}"
        api = GitHubAPI(os.environ.get("GITHUB_TOKEN", ""), repo)
        state, decision = execute(api, policy, number, head, base, url)
        if decision is not None:
            print(
                f"stage={decision.stage} exact_head={decision.head_sha} errors={len(decision.errors)}"
            )
            for error in decision.errors:
                print(f"BLOCKED: {error}")
        else:
            print("BLOCKED: trusted status could not be established")
        return 0 if state == "success" else 1
    except (AdmissionError, KeyError, TypeError, ValueError, OSError) as exc:
        print(
            f"BLOCKED: trusted gate initialization error ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
