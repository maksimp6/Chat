"""Read actual project-reader contracts through UTE; replace only HTTP transport."""

import json
from copy import deepcopy

import pytest
import requests

from scripts import logos_project_tools as project
from tool_providers.registry import ToolRegistry
from tool_providers.universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class Response:
    def __init__(self, body, *, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.content = body.encode() if isinstance(body, str) else json.dumps(body).encode()

    def json(self):
        return json.loads(self.content)


@pytest.fixture
def reader():
    calls, responses = [], []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, deepcopy(kwargs)))
            assert kwargs["allow_redirects"] is False
            assert kwargs["timeout"] == 60
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools, registry._categories = {}, {}
    project.register_project_reader("private-test-token", session=Session(), tool_registry=registry)
    executor = UniversalToolExecutor(registry)

    def execute(name, args):
        return executor.execute(UniversalToolCall(name, args, transport="responses_api"))

    return calls, responses, registry, execute


def test_auth_read_tool_uses_native_registry_and_retains_complete_large_json(reader):
    calls, responses, registry, execute = reader
    payload = {"body": "проект " * 25000 + "END_COMPLETE", "id": 1413}
    responses.append(Response(payload))
    result = execute("github_read_project", {"path": "issues/1413"})
    assert result["success"] is True
    assert result["data"]["content"] == payload
    assert result["data"]["truncated"] is False
    assert len(result["data"]["content"]["body"].encode()) > 65536
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://api.github.com/repos/maksimp6/Chat/issues/1413"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer private-test-token"
    assert "private-test-token" not in json.dumps(result)
    assert registry.get_universal_definition("github_read_project").read_only is True


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test",
        "//evil.test",
        "/repos/other/repo",
        "../other",
        "issues/../secrets",
        "issues/%2e%2e/other",
        "issues\\1413",
        "issues/1413#fragment",
        "issues/1413?access_token=secret",
        "issues?per_page=101",
        "issues?page=0",
        "issues?page=1&page=2",
        "issues?per_page=-2",
        "issues\nother",
    ],
)
def test_invalid_paths_never_reach_http(reader, path):
    calls, _, _, execute = reader
    result = execute("github_read_project", {"path": path})
    assert result["success"] is False
    assert calls == []


def test_rest_pagination_is_explicit_and_not_claimed_complete(reader):
    calls, responses, _, execute = reader
    link = '<https://api.github.com/repos/maksimp6/Chat/issues?per_page=100&page=2>; rel="next"'
    responses.append(
        Response([{"id": 1}], headers={"Content-Type": "application/json", "Link": link})
    )
    result = execute("github_read_project", {"path": "issues?per_page=100&page=1"})["data"]
    assert result["pagination"]["link"] == link
    assert result["pagination"]["complete"] is False
    assert len(calls) == 1


def test_source_reference_query_reads_requested_branch_without_changing_it(reader):
    calls, responses, _, execute = reader
    responses.append(Response({"sha": "a" * 40, "content": "Complete source"}))
    result = execute("github_read_project", {"path": "contents/README.md?ref=develop"})
    assert result["success"] is True
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("contents/README.md?ref=develop")


def test_job_log_redirect_never_sends_authorization_to_blob_host(reader):
    calls, responses, _, execute = reader
    signed = "https://productionresults.blob.core.windows.net/log/job?sig=PRIVATE_SIGNATURE"
    responses.extend(
        [
            Response("", status=302, headers={"Location": signed}),
            Response("first\nlast", headers={"Content-Type": "text/plain"}),
        ]
    )
    result = execute("github_read_project", {"path": "actions/jobs/123/logs"})
    assert result["success"] is True
    assert result["data"]["content"] == "first\nlast"
    assert "Authorization" not in calls[1][2]["headers"]
    assert "PRIVATE_SIGNATURE" not in json.dumps(result)
    assert result["data"]["source"].endswith("/actions/jobs/123/logs")


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/log",
        "http://productionresults.blob.core.windows.net/log",
        "https://api.github.com/repos/other/repo/issues",
        "https://evil.test@api.github.com/repos/maksimp6/Chat/issues",
        "https://productionresults.blob.core.windows.net:444/log",
    ],
)
def test_redirect_scope_rejects_host_or_repo_escape_before_second_request(reader, target):
    calls, responses, _, execute = reader
    responses.append(Response("", status=302, headers={"Location": target}))
    assert execute("github_read_project", {"path": "actions/jobs/123/logs"})["success"] is False
    assert len(calls) == 1


def test_secrets_in_successful_source_are_redacted_with_digests(reader):
    _, responses, _, execute = reader
    responses.append(Response({"body": "History private-test-token ghp_" + "x" * 40}))
    result = execute("github_read_project", {"path": "issues/1413"})["data"]
    assert "private-test-token" not in json.dumps(result)
    assert "ghp_" not in json.dumps(result)
    assert result["redactions"] == 2
    assert result["source_sha256"] != result["content_sha256"]


def test_transport_error_does_not_leak_credential_or_response_body(reader):
    _, responses, _, execute = reader
    responses.append(requests.ConnectionError("private-test-token raw reason"))
    result = execute("github_read_project", {"path": "issues/1413"})
    assert result["success"] is False
    assert "private-test-token" not in json.dumps(result)
    assert "raw reason" not in json.dumps(result)


def connection(nodes, after=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": after is not None, "endCursor": after}}


def discussion_page(nodes, after=None):
    return {
        "data": {
            "repository": {
                "nameWithOwner": "maksimp6/Chat",
                "discussion": {
                    "id": "D1",
                    "number": 25,
                    "title": "Project discussion",
                    "body": "Full dialogue",
                    "comments": connection(nodes, after),
                },
            }
        }
    }


def reply_page(nodes, after=None, repository="maksimp6/Chat"):
    return {
        "data": {
            "node": {
                "discussion": {"number": 25, "repository": {"nameWithOwner": repository}},
                "replies": connection(nodes, after),
            }
        }
    }


def test_discussion_follows_every_comment_and_reply_page_without_arbitrary_query(reader):
    calls, responses, _, execute = reader
    responses.extend(
        [
            Response(discussion_page([{"id": "C1", "body": "Comment1"}], "next-comment")),
            Response(discussion_page([{"id": "C2", "body": "Comment2"}])),
            Response(reply_page([{"id": "R1", "body": "Reply1"}], "next-reply")),
            Response(reply_page([{"id": "R2", "body": "Reply2"}])),
            Response(reply_page([])),
        ]
    )
    result = execute("github_read_discussion", {"number": 25})
    assert result["success"] is True
    content = result["data"]["content"]
    assert [comment["id"] for comment in content["comments"]] == ["C1", "C2"]
    assert [reply["id"] for reply in content["comments"][0]["replies"]] == ["R1", "R2"]
    assert result["data"]["pagination"]["complete"] is True
    assert len(calls) == 5
    assert all(call[0] == "POST" and call[1] == "https://api.github.com/graphql" for call in calls)
    assert {call[2]["json"]["query"] for call in calls} == {project._DISCUSSION, project._REPLIES}
    assert all("mutation" not in call[2]["json"]["query"] for call in calls)
    assert calls[1][2]["json"]["variables"]["after"] == "next-comment"
    assert calls[3][2]["json"]["variables"]["after"] == "next-reply"


def test_discussion_replies_from_foreign_project_are_rejected(reader):
    _, responses, _, execute = reader
    responses.extend(
        [
            Response(discussion_page([{"id": "C1", "body": "Comment"}])),
            Response(reply_page([{"id": "R1", "body": "foreign"}], repository="other/repo")),
        ]
    )
    result = execute("github_read_discussion", {"number": 25})
    assert result["success"] is False
    assert "foreign" not in json.dumps(result)


def test_graphql_errors_never_claim_complete_or_leak_details(reader):
    _, responses, _, execute = reader
    responses.append(Response({"errors": [{"message": "private-test-token permission denied"}]}))
    result = execute("github_read_discussion", {"number": 25})
    assert result["success"] is False
    assert "private-test-token" not in json.dumps(result)


def test_tool_contract_rejects_write_method_query_or_negative_discussion(reader):
    calls, _, _, execute = reader
    for name, args in [
        ("github_read_project", {"path": "issues", "method": "POST"}),
        ("github_read_discussion", {"number": -1}),
        ("github_read_discussion", {"number": 25, "query": "mutation{...}"}),
    ]:
        assert execute(name, args)["success"] is False
    assert calls == []


def test_absent_token_registers_public_reader_without_auth_or_empty_string_redaction():
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response({"body": "Public complete source"})

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools, registry._categories = {}, {}
    project.register_project_reader("", session=Session(), tool_registry=registry)
    result = UniversalToolExecutor(registry).execute(
        UniversalToolCall("github_read_project", {"path": "issues/1413"}, transport="responses_api")
    )
    assert result["success"] is True
    assert result["data"]["authenticated"] is False
    assert result["data"]["redactions"] == 0
    assert result["data"]["content"] == {"body": "Public complete source"}
    assert "Authorization" not in calls[0][2]["headers"]
