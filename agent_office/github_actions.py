"""Fail-closed GitHub Actions dispatch contract for trusted Coordinator mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Protocol
import urllib.error
import urllib.parse
import urllib.request


GITHUB_API_VERSION = "2026-03-10"
RDC_RESTART_WORKFLOW = ".github/workflows/cloudru-rdc.yml"
RDC_RESTART_WORKFLOW_ID = "cloudru-rdc.yml"
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,200}")
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class WorkflowDispatchError(RuntimeError):
    """A sanitized fail-closed workflow-dispatch failure."""


@dataclass(frozen=True)
class DispatchApproval:
    """Approval evidence already authorized by the caller's policy boundary."""

    work_order_id: str
    approver_id: str
    workflow: str
    ref_sha: str
    action: str
    idempotency_key: str


@dataclass(frozen=True)
class DispatchRequest:
    """One fully validated, allowlisted workflow dispatch."""

    workflow: str
    workflow_id: str
    ref_sha: str
    inputs: tuple[tuple[str, str], ...]
    work_order_id: str
    approver_id: str
    idempotency_key: str

    @property
    def input_map(self) -> dict[str, str]:
        return dict(self.inputs)

    @property
    def fingerprint(self) -> str:
        payload = {
            "workflow": self.workflow,
            "ref_sha": self.ref_sha,
            "inputs": self.input_map,
            "work_order_id": self.work_order_id,
            "approver_id": self.approver_id,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class DispatchResult:
    """Sanitized identity returned by GitHub for the created run."""

    workflow_run_id: int
    run_url: str
    html_url: str


@dataclass
class InMemoryDispatchLedger:
    """Test/local ledger; production persistence belongs to the canonical state layer."""

    _entries: dict[str, tuple[str, DispatchResult]] = field(default_factory=dict, repr=False)

    def lookup(self, key: str) -> tuple[str, DispatchResult] | None:
        return self._entries.get(key)

    def remember(self, key: str, fingerprint: str, result: DispatchResult) -> None:
        self._entries[key] = (fingerprint, result)


class DispatchTransport(Protocol):
    def dispatch(self, repository: str, request: DispatchRequest) -> DispatchResult: ...


def _require_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise WorkflowDispatchError(f"invalid_{field_name}")
    return value


def build_rdc_restart_request(
    *,
    protected_head_sha: str,
    approval: DispatchApproval,
) -> DispatchRequest:
    """Build the only production mutation admitted by the first #823 slice."""

    if not isinstance(protected_head_sha, str) or not _SHA_RE.fullmatch(protected_head_sha):
        raise WorkflowDispatchError("invalid_protected_head_sha")
    if approval.workflow != RDC_RESTART_WORKFLOW:
        raise WorkflowDispatchError("workflow_not_allowed")
    if approval.action != "restart":
        raise WorkflowDispatchError("workflow_input_not_allowed")
    if approval.ref_sha != protected_head_sha:
        raise WorkflowDispatchError("stale_or_unapproved_ref")

    work_order_id = _require_identifier(approval.work_order_id, field_name="work_order_id")
    approver_id = _require_identifier(approval.approver_id, field_name="approver_id")
    idempotency_key = _require_identifier(approval.idempotency_key, field_name="idempotency_key")

    return DispatchRequest(
        workflow=RDC_RESTART_WORKFLOW,
        workflow_id=RDC_RESTART_WORKFLOW_ID,
        ref_sha=protected_head_sha,
        inputs=(("action", "restart"),),
        work_order_id=work_order_id,
        approver_id=approver_id,
        idempotency_key=idempotency_key,
    )


def _validate_dispatch_request(repository: str, request: DispatchRequest) -> None:
    if not _REPOSITORY_RE.fullmatch(repository):
        raise WorkflowDispatchError("invalid_repository")
    if request.workflow != RDC_RESTART_WORKFLOW or request.workflow_id != RDC_RESTART_WORKFLOW_ID:
        raise WorkflowDispatchError("workflow_not_allowed")
    if request.input_map != {"action": "restart"}:
        raise WorkflowDispatchError("workflow_input_not_allowed")
    if not _SHA_RE.fullmatch(request.ref_sha):
        raise WorkflowDispatchError("invalid_ref_sha")


def _read_dispatch_response(opener, http_request: urllib.request.Request) -> tuple[int | None, bytes]:
    try:
        with opener(http_request, timeout=20) as response:
            return getattr(response, "status", None), response.read(65537)
    except urllib.error.HTTPError as exc:
        raise WorkflowDispatchError(f"github_dispatch_http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WorkflowDispatchError("github_dispatch_transport_error") from None


def _decode_dispatch_response(repository: str, status: int | None, body: bytes) -> DispatchResult:
    if status != 200 or len(body) > 65536:
        raise WorkflowDispatchError("github_dispatch_invalid_response")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise WorkflowDispatchError("github_dispatch_invalid_response") from None
    if not isinstance(value, dict):
        raise WorkflowDispatchError("github_dispatch_invalid_response")

    run_id = value.get("workflow_run_id")
    run_url = value.get("run_url")
    html_url = value.get("html_url")
    expected_api_prefix = f"https://api.github.com/repos/{repository}/actions/runs/"
    expected_html_prefix = f"https://github.com/{repository}/actions/runs/"
    valid = (
        type(run_id) is int
        and run_id > 0
        and isinstance(run_url, str)
        and run_url.startswith(expected_api_prefix)
        and isinstance(html_url, str)
        and html_url.startswith(expected_html_prefix)
    )
    if not valid:
        raise WorkflowDispatchError("github_dispatch_invalid_response")
    return DispatchResult(run_id, run_url, html_url)


class GitHubWorkflowDispatchClient:
    """Minimal GitHub REST adapter. The short-lived token never enters results/errors."""

    def __init__(
        self,
        token: str,
        *,
        api_root: str = "https://api.github.com",
        opener=urllib.request.urlopen,
    ) -> None:
        if not isinstance(token, str) or not token:
            raise WorkflowDispatchError("github_token_missing")
        self._token = token
        self._api_root = api_root.rstrip("/")
        self._opener = opener

    def dispatch(self, repository: str, request: DispatchRequest) -> DispatchResult:
        _validate_dispatch_request(repository, request)
        workflow_id = urllib.parse.quote(request.workflow_id, safe="")
        url = f"{self._api_root}/repos/{repository}/actions/workflows/{workflow_id}/dispatches"
        payload = json.dumps(
            {
                "ref": request.ref_sha,
                "inputs": request.input_map,
                "return_run_details": True,
            },
            separators=(",", ":"),
        ).encode()
        http_request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        status, body = _read_dispatch_response(self._opener, http_request)
        return _decode_dispatch_response(repository, status, body)


def dispatch_once(
    *,
    repository: str,
    request: DispatchRequest,
    transport: DispatchTransport,
    ledger: InMemoryDispatchLedger,
) -> DispatchResult:
    """Dispatch once per approved idempotency key, rejecting key reuse for new work."""

    previous = ledger.lookup(request.idempotency_key)
    if previous is not None:
        fingerprint, result = previous
        if fingerprint != request.fingerprint:
            raise WorkflowDispatchError("idempotency_key_conflict")
        return result

    result = transport.dispatch(repository, request)
    ledger.remember(request.idempotency_key, request.fingerprint, result)
    return result
