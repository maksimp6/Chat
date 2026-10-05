from dataclasses import replace
import io
import json
import urllib.error

import pytest

from agent_office.github_actions import (
    DispatchApproval,
    DispatchResult,
    GitHubWorkflowDispatchClient,
    InMemoryDispatchLedger,
    RDC_RESTART_WORKFLOW,
    WorkflowDispatchError,
    build_rdc_restart_request,
    dispatch_once,
)


SHA = "a" * 40


def approval(**overrides):
    values = {
        "work_order_id": "issue-787",
        "approver_id": "owner-1",
        "workflow": RDC_RESTART_WORKFLOW,
        "ref_sha": SHA,
        "action": "restart",
        "idempotency_key": "issue-787:restart:" + SHA,
    }
    values.update(overrides)
    return DispatchApproval(**values)


def test_rdc_restart_request_is_exact_allowlist():
    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())

    assert request.workflow == ".github/workflows/cloudru-rdc.yml"
    assert request.workflow_id == "cloudru-rdc.yml"
    assert request.ref_sha == SHA
    assert request.input_map == {"action": "restart"}


@pytest.mark.parametrize(
    ("changed", "error"),
    [
        ({"workflow": ".github/workflows/production-deploy.yml"}, "workflow_not_allowed"),
        ({"action": "stop"}, "workflow_input_not_allowed"),
        ({"ref_sha": "b" * 40}, "stale_or_unapproved_ref"),
    ],
)
def test_request_rejects_scope_or_approval_drift(changed, error):
    with pytest.raises(WorkflowDispatchError, match=error):
        build_rdc_restart_request(protected_head_sha=SHA, approval=approval(**changed))


def test_request_rejects_non_exact_protected_sha():
    with pytest.raises(WorkflowDispatchError, match="invalid_protected_head_sha"):
        build_rdc_restart_request(protected_head_sha="master", approval=approval())


class FakeResponse:
    status = 200

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self._body


def test_github_client_posts_only_allowlisted_dispatch_and_returns_run_identity():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return FakeResponse(
            json.dumps(
                {
                    "workflow_run_id": 123,
                    "run_url": "https://api.github.com/repos/maksimp6/Chat/actions/runs/123",
                    "html_url": "https://github.com/maksimp6/Chat/actions/runs/123",
                }
            ).encode()
        )

    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("top-secret-token", opener=opener)
    result = client.dispatch("maksimp6/Chat", request)

    assert result == DispatchResult(
        123,
        "https://api.github.com/repos/maksimp6/Chat/actions/runs/123",
        "https://github.com/maksimp6/Chat/actions/runs/123",
    )
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/actions/workflows/cloudru-rdc.yml/dispatches")
    assert seen["body"] == {
        "ref": SHA,
        "inputs": {"action": "restart"},
        "return_run_details": True,
    }
    assert seen["timeout"] == 20
    assert seen["headers"]["Authorization"] == "Bearer top-secret-token"
    assert "top-secret-token" not in repr(result)


def test_github_client_sanitizes_provider_error_body():
    secret = "provider-secret-body"

    def opener(_request, timeout):
        assert timeout == 20
        raise urllib.error.HTTPError(
            "https://api.github.com/example",
            403,
            secret,
            {},
            io.BytesIO(secret.encode()),
        )

    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=opener)

    with pytest.raises(WorkflowDispatchError) as caught:
        client.dispatch("maksimp6/Chat", request)

    assert str(caught.value) == "github_dispatch_http_403"
    assert secret not in str(caught.value)


def test_github_client_rejects_ambiguous_run_identity():
    def opener(_request, timeout):
        assert timeout == 20
        return FakeResponse(
            json.dumps(
                {
                    "workflow_run_id": 123,
                    "run_url": "https://evil.example/run/123",
                    "html_url": "https://github.com/maksimp6/Chat/actions/runs/123",
                }
            ).encode()
        )

    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=opener)

    with pytest.raises(WorkflowDispatchError, match="github_dispatch_invalid_response"):
        client.dispatch("maksimp6/Chat", request)


@pytest.mark.parametrize("field_name", ["work_order_id", "approver_id", "idempotency_key"])
def test_request_rejects_invalid_approval_identifiers(field_name):
    with pytest.raises(WorkflowDispatchError, match=f"invalid_{field_name}"):
        build_rdc_restart_request(
            protected_head_sha=SHA,
            approval=approval(**{field_name: "bad value with spaces"}),
        )


@pytest.mark.parametrize(
    ("mutator", "repository", "error"),
    [
        (lambda request: request, "not-a-repository", "invalid_repository"),
        (
            lambda request: replace(request, workflow=".github/workflows/other.yml"),
            "maksimp6/Chat",
            "workflow_not_allowed",
        ),
        (
            lambda request: replace(request, inputs=(("action", "stop"),)),
            "maksimp6/Chat",
            "workflow_input_not_allowed",
        ),
        (
            lambda request: replace(request, ref_sha="master"),
            "maksimp6/Chat",
            "invalid_ref_sha",
        ),
    ],
)
def test_github_client_revalidates_boundary_inputs(mutator, repository, error):
    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=lambda *_args, **_kwargs: None)

    with pytest.raises(WorkflowDispatchError, match=error):
        client.dispatch(repository, mutator(request))


def test_github_client_requires_token():
    with pytest.raises(WorkflowDispatchError, match="github_token_missing"):
        GitHubWorkflowDispatchClient("")


def test_github_client_sanitizes_transport_failure():
    def opener(_request, timeout):
        assert timeout == 20
        raise urllib.error.URLError("provider detail")

    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=opener)

    with pytest.raises(WorkflowDispatchError, match="github_dispatch_transport_error"):
        client.dispatch("maksimp6/Chat", request)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"not-json"),
        FakeResponse(json.dumps(["not", "an", "object"]).encode()),
    ],
)
def test_github_client_rejects_malformed_response_body(response):
    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=lambda *_args, **_kwargs: response)

    with pytest.raises(WorkflowDispatchError, match="github_dispatch_invalid_response"):
        client.dispatch("maksimp6/Chat", request)


def test_github_client_rejects_non_success_status():
    response = FakeResponse(b"{}")
    response.status = 204
    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    client = GitHubWorkflowDispatchClient("token", opener=lambda *_args, **_kwargs: response)

    with pytest.raises(WorkflowDispatchError, match="github_dispatch_invalid_response"):
        client.dispatch("maksimp6/Chat", request)


class CountingTransport:
    def __init__(self):
        self.calls = 0

    def dispatch(self, _repository, _request):
        self.calls += 1
        return DispatchResult(
            99,
            "https://api.github.com/repos/maksimp6/Chat/actions/runs/99",
            "https://github.com/maksimp6/Chat/actions/runs/99",
        )


def test_dispatch_once_deduplicates_same_approved_operation():
    ledger = InMemoryDispatchLedger()
    transport = CountingTransport()
    request = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())

    first = dispatch_once(
        repository="maksimp6/Chat",
        request=request,
        transport=transport,
        ledger=ledger,
    )
    second = dispatch_once(
        repository="maksimp6/Chat",
        request=request,
        transport=transport,
        ledger=ledger,
    )

    assert first == second
    assert transport.calls == 1


def test_dispatch_once_rejects_idempotency_key_reuse_for_different_work():
    ledger = InMemoryDispatchLedger()
    transport = CountingTransport()
    first = build_rdc_restart_request(protected_head_sha=SHA, approval=approval())
    dispatch_once(repository="maksimp6/Chat", request=first, transport=transport, ledger=ledger)

    changed = build_rdc_restart_request(
        protected_head_sha="b" * 40,
        approval=approval(
            ref_sha="b" * 40,
            idempotency_key=first.idempotency_key,
        ),
    )

    with pytest.raises(WorkflowDispatchError, match="idempotency_key_conflict"):
        dispatch_once(
            repository="maksimp6/Chat",
            request=changed,
            transport=transport,
            ledger=ledger,
        )

    assert transport.calls == 1
