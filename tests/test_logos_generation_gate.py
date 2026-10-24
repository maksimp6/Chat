"""Court decision #1413 D0: native Logos generation is closed without owner permission.

The workflow job conditions are evaluated against representative event payloads,
and the CLI entrypoints are run without any provider: a blocked request must reach
neither credential seeding nor a client nor a model call, while reading a stored
response stays available.
"""

import json
import re
from pathlib import Path

import pytest
import requests
import yaml

import agent_runtime.issue_runner as issue_runner
from agent_runtime.generation_gate import GENERATION_ENV, generation_allowed
from scripts import logos_dialogue as dialogue

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"\s+|('(?:[^']|'')*')|(&&|\|\||==|!=|!|\(|\)|,)|([A-Za-z_][\w.-]*)")


class _Text(str):
    """Actions compares strings ignoring case."""

    def __eq__(self, other):
        return isinstance(other, str) and self.casefold() == other.casefold()

    def __ne__(self, other):
        return not self == other

    __hash__ = str.__hash__


def _contains(haystack, needle):
    if isinstance(haystack, list):
        return any(_Text(item) == needle for item in haystack)
    return str(needle or "").casefold() in str(haystack or "").casefold()


def evaluate(expression, context):
    """Evaluate the small Actions expression subset used by these job conditions."""
    python = []
    position = 0
    while position < len(expression):
        match = TOKEN.match(expression, position)
        assert match, expression[position:]
        literal, operator, name = match.groups()
        position = match.end()
        if literal:
            text = literal[1:-1].replace("''", "'")
            python.append(f"_Text({text!r})")
        elif operator:
            python.append({"&&": " and ", "||": " or ", "!": " not "}.get(operator, operator))
        elif name in ("contains", "fromJSON"):
            python.append({"contains": "_contains", "fromJSON": "json.loads"}[name])
        elif name:
            python.append(f"_ctx({name!r})")

    def lookup(path):
        value = context
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return _Text(value) if isinstance(value, str) else value

    scope = {"_Text": _Text, "_contains": _contains, "_ctx": lookup, "json": json}
    # Test-only: the evaluated text is the repository's own workflow condition.
    return bool(eval("".join(python), scope))


def job_condition(workflow, job):
    data = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return data["jobs"][job]["if"], data["jobs"][job]


def owner_comment(variable):
    return {
        "github": {
            "event_name": "issue_comment",
            "event": {
                "action": "created",
                "issue": {"title": "t", "body": "b"},
                "comment": {"body": "@logos please", "author_association": "OWNER"},
            },
        },
        "vars": variable,
    }


def owner_issue(variable):
    return {
        "github": {
            "event_name": "issues",
            "event": {
                "action": "opened",
                "issue": {"title": "@Logos task", "body": "", "author_association": "OWNER"},
            },
        },
        "vars": variable,
    }


def alice_label(variable):
    return {
        "github": {
            "event_name": "issues",
            "event": {"action": "labeled", "issue": {}, "label": {"name": "alice"}},
        },
        "vars": variable,
    }


CLOSED = ({}, {GENERATION_ENV: ""}, {GENERATION_ENV: "false"}, {GENERATION_ENV: "1"})
OPEN = {GENERATION_ENV: "true"}


@pytest.mark.parametrize("event", [owner_comment, owner_issue, alice_label])
@pytest.mark.parametrize("variable", CLOSED)
def test_issue_entrypoints_do_not_start_logos_without_permission(event, variable):
    condition, _ = job_condition("logos.yml", "alice")
    assert evaluate(condition, event(variable)) is False


@pytest.mark.parametrize("event", [owner_comment, owner_issue, alice_label])
def test_issue_entrypoints_still_start_logos_with_permission(event):
    condition, _ = job_condition("logos.yml", "alice")
    assert evaluate(condition, event(OPEN)) is True


def test_permission_does_not_widen_the_existing_trigger_rules():
    condition, _ = job_condition("logos.yml", "alice")
    stranger = owner_comment(OPEN)
    stranger["github"]["event"]["comment"]["author_association"] = "NONE"
    pull_request = owner_comment(OPEN)
    pull_request["github"]["event"]["issue"]["pull_request"] = {"url": "x"}
    unrelated = owner_comment(OPEN)
    unrelated["github"]["event"]["comment"]["body"] = "court decision, no trigger"
    for context in (stranger, pull_request, unrelated):
        assert evaluate(condition, context) is False


def dispatch(mode, variable):
    return {
        "github": {"event_name": "workflow_dispatch"},
        "inputs": {"mode": mode},
        "vars": variable,
    }


@pytest.mark.parametrize("variable", CLOSED)
def test_manual_dialogue_needs_permission_but_reading_stays_available(variable):
    condition, _ = job_condition("rag-work.yml", "logos")
    assert evaluate(condition, dispatch("logos", variable)) is False
    assert evaluate(condition, dispatch("logos", OPEN)) is True
    for mode in ("logos-response", "logos-tools"):
        assert evaluate(condition, dispatch(mode, variable)) is True
    for job in ("rag", "logos-result"):
        other, _ = job_condition("rag-work.yml", job)
        assert evaluate(other, dispatch("logos", OPEN)) is False


def test_runner_steps_receive_the_same_permission_variable():
    expected = "${{ vars.LOGOS_GENERATION_ALLOWED }}"
    _, alice = job_condition("logos.yml", "alice")
    assert alice["env"][GENERATION_ENV] == expected
    _, logos = job_condition("rag-work.yml", "logos")
    step = next(s for s in logos["steps"] if s["name"].startswith("Run Logos dialogue"))
    assert step["env"][GENERATION_ENV] == expected


@pytest.mark.parametrize(
    ("value", "allowed"),
    [(None, False), ("", False), ("false", False), ("1", False), (" true", False)]
    + [("true", True), ("TRUE", True)],
)
def test_permission_value(value, allowed):
    environ = {} if value is None else {GENERATION_ENV: value}
    assert generation_allowed(environ) is allowed


@pytest.fixture
def no_provider(monkeypatch):
    import chat.mcp_routes as mcp_routes

    def reject(*args, **kwargs):
        pytest.fail("A blocked generation must not reach a credential, client or provider")

    monkeypatch.setattr(requests.Session, "request", reject)
    monkeypatch.setattr(issue_runner, "seed_provider_credential", reject)
    monkeypatch.setattr(mcp_routes, "LogosClient", reject)
    monkeypatch.setattr(issue_runner, "run_issue_task", reject)
    monkeypatch.setattr(dialogue, "run_dialogue", reject)


def issue_cli(tmp_path):
    for name in ("title.txt", "body.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    return [
        "--issue",
        "1",
        "--title-file",
        str(tmp_path / "title.txt"),
        "--body-file",
        str(tmp_path / "body.txt"),
        "--out",
        str(tmp_path / "out.json"),
    ]


@pytest.mark.parametrize("value", [None, "false"])
def test_issue_runner_refuses_before_any_model_request(tmp_path, monkeypatch, no_provider, value):
    if value is None:
        monkeypatch.delenv(GENERATION_ENV, raising=False)
    else:
        monkeypatch.setenv(GENERATION_ENV, value)

    assert issue_runner.main(issue_cli(tmp_path)) == 1
    result = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert result == {"status": "blocked", "error": "GenerationNotPermitted", "changed_files": []}


def test_issue_runner_runs_once_with_permission(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv(GENERATION_ENV, "true")
    monkeypatch.setattr(
        issue_runner,
        "run_issue_task",
        lambda *args, **kwargs: calls.append(args) or {"status": "no_changes", "changed_files": []},
    )

    assert issue_runner.main(issue_cli(tmp_path)) == 0
    assert len(calls) == 1


def test_dialogue_refuses_before_context_or_model(tmp_path, monkeypatch, no_provider):
    monkeypatch.delenv(GENERATION_ENV, raising=False)
    question = tmp_path / "question.txt"
    question.write_text("вопрос", encoding="utf-8")
    output = tmp_path / "answer.json"

    assert dialogue.main(["--question-file", str(question), "--out", str(output)]) == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "blocked" and result["error"] == "GenerationNotPermitted"
    assert result["generation_requests"] == 0 and result["response_ids"] == []


def test_stored_response_stays_readable_without_permission(tmp_path, monkeypatch, no_provider):
    monkeypatch.delenv(GENERATION_ENV, raising=False)
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    read = []
    monkeypatch.setattr(
        dialogue,
        "inspect_response",
        lambda value: read.append(value) or {"status": "retrieved", "generation_requests": 0},
    )

    assert dialogue.main(["--response-id", response_id, "--out", str(tmp_path / "a.json")]) == 0
    assert read == [response_id]
