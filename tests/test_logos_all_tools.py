"""Every tool offered to Logos must be executable where and how it is offered."""

import json
import subprocess
from pathlib import Path

import pytest

from chat import mcp_routes
from chat.mcp_routes import LogosClient
from organization import government
from scripts import logos_dialogue as dialogue
from scripts.logos_quality import dialogue_params
from tests.test_logos_dialogue import (  # noqa: F401 -- shared real-runtime fixtures
    native_response,
    native_transport,
    no_network,
    prepared_context,
    repo,
)
from tool_providers import git as git_tools
from tool_providers import registry as registry_module
from tool_providers import termux, wikipedia
from tool_providers.registry import ToolRegistry, registry
from tool_providers.universal_tool_platform import (
    UniversalToolCall,
    UniversalToolExecutor,
    validate_json_schema,
)
from yandex_client_modules.client import YandexResponsesClient


def example(schema):
    """Arguments a strict-mode model sends: required values, null for unused options."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    options = schema.get("anyOf")
    if options:
        if any(option.get("type") == "null" for option in options):
            return None
        return example(options[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(item for item in kind if item != "null")
    if kind == "object":
        return {name: example(item) for name, item in (schema.get("properties") or {}).items()}
    if kind == "array":
        return [example(schema.get("items") or {})] * int(schema.get("minItems") or 0)
    if kind == "string":
        return "x" * max(1, int(schema.get("minLength") or 1))
    if kind in {"integer", "number"}:
        return max(1, schema.get("minimum") or 1)
    if kind == "boolean":
        return False
    return None


def test_every_offered_tool_accepts_arguments_shaped_by_its_advertised_schema():
    executor = UniversalToolExecutor(registry)
    offered = registry.get_definitions()
    assert len(offered) > 60
    for tool in offered:
        arguments = example(tool["parameters"])
        assert not validate_json_schema(arguments, tool["parameters"]), tool["name"]
        # The deny policy runs after validation and before any side effect.
        result = executor.execute(
            UniversalToolCall(tool["name"], arguments, transport="responses_api"),
            policy=lambda definition, call: False,
        )
        assert result["metadata"]["phase"] == "authorization", (tool["name"], result)
        assert result["error"] == "Tool policy denied"


def isolated_registry(**tools):
    target = ToolRegistry.__new__(ToolRegistry)
    target._tools, target._categories = {}, {}
    for name, cfg in tools.items():
        target.register("sample", name, {"func": lambda arguments: {"ok": True}, **cfg})
    return target


def test_tools_the_transport_or_host_cannot_execute_are_not_offered(monkeypatch):
    target = isolated_registry(
        mcp_only={"supported_transports": ["mcp"]},
        device={"metadata": {"requires_executables": ["termux-toast"]}},
        local={},
    )
    monkeypatch.setattr(registry_module.shutil, "which", lambda name: None)
    assert [tool["name"] for tool in target.get_definitions()] == ["local"]
    assert [tool["name"] for tool in target.get_tools_by_category("sample")] == ["local"]

    monkeypatch.setattr(registry_module.shutil, "which", lambda name: "/usr/bin/" + name)
    assert [tool["name"] for tool in target.get_tools_by_category("sample")] == ["device", "local"]


def test_real_mcp_only_and_termux_tools_are_withheld_from_responses(monkeypatch):
    import mcp_server.tools  # noqa: F401 -- registers the MCP-only ChatGPT bridge tools

    monkeypatch.setattr(registry_module.shutil, "which", lambda name: None)
    offered = {tool["name"] for tool in registry.get_definitions()}
    assert "logos_read_project_file" in registry._tools
    assert not {name for name in offered if name.startswith("logos_")}
    assert not {name for name in offered if name.startswith("termux_")}
    assert {"remove_file", "git_status", "wikipedia_search", "read_file"} <= offered


def test_free_form_object_stays_open_and_disables_strict_mode():
    target = isolated_registry(
        open_data={
            "parameters": {
                "type": "object",
                "properties": {"case_id": {"type": "string"}, "data": {"type": "object"}},
                "required": ["case_id", "data"],
            }
        },
        closed={"parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    )
    tools = {tool["name"]: tool for tool in target.get_definitions()}
    assert tools["open_data"]["strict"] is False
    assert tools["closed"]["strict"] is True
    payload = {"case_id": "gov-1", "data": {"full_name": "Test User", "passport": "X"}}
    assert validate_json_schema(payload, tools["open_data"]["parameters"]) == []
    # The real government contract can now receive the applicant fields.
    collect = next(
        tool for tool in registry.get_definitions() if tool["name"] == "gov.collect_data"
    )
    assert collect["strict"] is False
    assert validate_json_schema(payload, collect["parameters"]) == []


def batch_transport(monkeypatch, *responses):
    sent = []
    pending = iter(responses)

    def transport(self, **kwargs):
        sent.append(kwargs)
        return next(pending)

    monkeypatch.setattr(YandexResponsesClient, "ask_with_mcp", transport)
    monkeypatch.setattr(mcp_routes.mcp_storage, "list_servers", lambda **_owner: [])
    return sent


def mixed_batch():
    return {
        "id": "response-1",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "free",
                "name": "read_file",
                "arguments": '{"path": "README.md", "length": 64}',
            },
            {
                "type": "function_call",
                "call_id": "gated",
                "name": "apply_patch",
                "arguments": '{"path": "README.md", "search_text": "L", "replace_text": "X"}',
            },
        ],
    }


FINAL = {"id": "response-2", "status": "completed", "output": [{"type": "message", "content": []}]}


def test_deny_mode_runs_free_calls_and_refuses_only_the_gated_one(repo, monkeypatch):
    before = (repo / "README.md").read_bytes()
    sent = batch_transport(monkeypatch, mixed_batch(), FINAL)

    result = object.__new__(LogosClient).ask_with_mcp(
        "work", "model", params={"approval_mode": "deny"}
    )

    assert result is FINAL
    assert (repo / "README.md").read_bytes() == before
    outputs = {item["call_id"]: item for item in sent[1]["params"]["input"][-2:]}
    assert json.loads(outputs["free"]["output"])["content"] == before[:64].decode()
    refused = json.loads(outputs["gated"]["output"])
    assert refused["executed"] is False and refused["tool"] == "apply_patch"
    assert refused["error"] == "Tool approval required"


def test_deny_mode_stops_when_the_model_only_repeats_gated_calls(repo, monkeypatch):
    retry = mixed_batch()
    retry["id"] = "response-retry"
    retry["output"] = retry["output"][1:]
    sent = batch_transport(monkeypatch, mixed_batch(), retry, FINAL)

    result = object.__new__(LogosClient).ask_with_mcp(
        "work", "model", params={"approval_mode": "deny", "max_rounds": None}
    )

    # No progress is possible without an approver: stop instead of looping on refusals.
    assert result is retry and len(sent) == 2
    assert (repo / "README.md").read_text() == "Logos repository context.\n"


def test_default_mode_still_pauses_the_whole_batch_for_an_interactive_approver(monkeypatch):
    sent = batch_transport(monkeypatch, mixed_batch())
    executed = []
    monkeypatch.setattr(
        LogosClient, "_execute_single_tool", lambda self, call, *a, **k: executed.append(call)
    )

    result = object.__new__(LogosClient).ask_with_mcp("work", "model", params={})

    assert result["id"] == "response-1" and executed == [] and len(sent) == 1


def test_native_dialogue_refuses_gated_tools_and_reports_them(repo, native_transport):
    assert dialogue_params(["store"], ["filesystem"])["approval_mode"] == "deny"
    packet, knowledge = prepared_context(repo)
    client, calls = native_transport(mixed_batch(), native_response(2))

    result = dialogue.run_dialogue(
        "Прочитай README и исправь его.",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: client,
    )

    assert result["status"] == "completed"
    assert result["approval_refused_tools"] == ["apply_patch"]
    assert {
        "name": "apply_patch",
        "call_id": "gated",
        "success": False,
        "approval_required": True,
    } in result["tool_calls"]
    assert (repo / "README.md").read_text() == "Logos repository context.\n"
    gated = next(
        item
        for item in calls[2][1]["input"]
        if item.get("call_id") == "gated" and item["type"] == "function_call_output"
    )
    assert json.loads(gated["output"])["executed"] is False


def init_repository(root: Path) -> Path:
    root.mkdir()
    for command in (
        ["init", "-q", "-b", "feature"],
        [
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *command], check=True)
    return root


def test_git_requires_an_explicit_repository_and_pushes_the_current_branch(tmp_path, monkeypatch):
    project = init_repository(tmp_path / "checkout")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
    monkeypatch.chdir(tmp_path)
    no_device = {"repos_base_dir": str(tmp_path / "sdcard-missing")}

    unconfigured = git_tools.git_status({}, no_device)
    assert unconfigured["success"] is False
    assert "Репозиторий не задан" in unconfigured["error"]

    status = git_tools.git_status({"repo": str(project)}, no_device)
    assert status["success"] and "feature" in status["output"]
    pushed = git_tools.git_push({"repo": str(project)}, no_device)
    assert pushed["success"], pushed
    heads = subprocess.check_output(["git", "-C", str(remote), "branch"], text=True)
    assert heads.split() == ["feature"]
    # An explicitly named repository is never silently replaced.
    assert git_tools.git_status({"repo_path": "missing-repo"}, no_device)["success"] is False


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload, headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def test_wikipedia_is_read_only_validates_language_and_survives_rate_limits(monkeypatch):
    seen = []
    replies = iter(
        [
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, {"query": {"search": [{"title": "Python", "snippet": "язык"}]}}),
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(429, headers={"Retry-After": "0"}),
        ]
    )

    def get(url, **kwargs):
        seen.append((url, kwargs))
        return next(replies)

    monkeypatch.setattr(wikipedia.requests, "get", get)
    for name in ("wikipedia_search", "wikipedia_summary"):
        definition = registry.get_universal_definition(name)
        assert definition.read_only and not definition.requires_approval

    assert wikipedia.wikipedia_search({"query": "Python"})["results"][0]["title"] == "Python"
    assert seen[0][1]["headers"]["User-Agent"].startswith("Logos/1.0 (https://github.com/")
    limited = wikipedia.wikipedia_search({"query": "Python", "lang": "en"})
    assert limited == {
        "success": False,
        "error": "Wikipedia ограничила частоту запросов (HTTP 429); повторите позже",
        "status": 429,
    }
    assert len(seen) == 4

    result = UniversalToolExecutor(registry).execute(
        UniversalToolCall(
            "wikipedia_search", {"query": "x", "lang": "evil.example/"}, transport="responses_api"
        )
    )
    assert result["metadata"]["phase"] == "validation" and len(seen) == 4


def test_wikipedia_summary_quotes_the_whole_title(monkeypatch):
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        return FakeResponse(200, {"title": "AC/DC", "extract": "Band", "content_urls": {}})

    monkeypatch.setattr(wikipedia.requests, "get", get)
    assert wikipedia.wikipedia_summary({"title": "AC/DC live", "lang": "en"})["title"] == "AC/DC"
    assert seen == ["https://en.wikipedia.org/api/rest_v1/page/summary/AC%2FDC_live"]


def test_profiler_runs_without_approval_and_rejects_unknown_sort():
    executor = UniversalToolExecutor(registry)
    report = executor.execute(
        UniversalToolCall(
            "profile_application_performance",
            {"sort": None, "limit": None},
            transport="responses_api",
        )
    )
    assert report["success"] and report["data"]["memory_objects_count"] > 0
    invalid = executor.execute(
        UniversalToolCall(
            "profile_application_performance", {"sort": "bogus"}, transport="responses_api"
        )
    )
    assert invalid["metadata"]["phase"] == "validation"


def test_termux_tool_off_device_reports_the_missing_helper(monkeypatch):
    monkeypatch.setattr(termux.shutil, "which", lambda name: None)
    result = termux.termux_toast({"message": "hi"}, {})
    assert result["success"] is False
    assert result["error"].startswith("Утилита 'termux-toast' не найдена")


def test_approved_government_submission_completes_the_case(monkeypatch):
    monkeypatch.setenv("LOGOS_OWNER_ID", "owner-1")
    executor = UniversalToolExecutor(registry)

    def run(name, arguments, approved=False):
        return executor.execute(
            UniversalToolCall(name, arguments, transport="responses_api", approved=approved)
        )

    created = run("gov.case.create", {"case_type": "IP_REGISTRATION", "user_id": "spoofed"})
    case_id = created["data"]["case_id"]
    assert created["data"]["user_id"] == "owner-1"
    applicant = {"full_name": "T", "birth_date": "1990-01-01", "citizenship": "RU", "passport": "P"}
    assert run("gov.collect_data", {"case_id": case_id, "data": applicant}, approved=True)[
        "success"
    ]
    assert run("gov.prepare_documents", {"case_id": case_id})["success"]

    assert run("gov.submit", {"case_id": case_id})["metadata"]["phase"] == "approval_required"
    assert government.get_case(case_id)["status"] == "READY_FOR_APPROVAL"
    with pytest.raises(PermissionError):
        government.submit_case(case_id)

    submitted = run("gov.submit", {"case_id": case_id}, approved=True)
    assert submitted["success"], submitted
    assert submitted["data"]["status"] == "SUBMITTED" and submitted["data"]["user_approved"]


def test_web_chat_defaults_to_every_registered_category(monkeypatch):
    import yandex_client_modules.mcp_mixin as mcp_mixin

    requested = []
    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda **_owner: [])
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda conversation_id: None)
    monkeypatch.setattr(
        mcp_mixin.registry,
        "get_tools_by_category",
        lambda category: requested.append(category) or [],
    )

    class Capturing(mcp_mixin.YandexMcpMixin):
        def ask(self, *args, **kwargs):
            return {"output": []}

    Capturing().ask_with_mcp("hello", "logos-runtime")
    assert requested == list(registry.get_available_categories())
    assert {"theme", "cloud", "government", "partner", "3d"} <= set(requested)
