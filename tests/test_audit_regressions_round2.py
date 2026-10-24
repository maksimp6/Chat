"""Regression tests for the second batch of audit fixes: quotas, provider headers,
observer login matching, budget wins, the native project-board journal and trace locking."""

import threading
from types import SimpleNamespace

import pytest

from agent_office.observer import classify_login
from economy.budget_controller import AccountType, BudgetController
from observability.trace_manager import ExecutionTrace
from project_board.store import BoardStore
from provider_accounts.quotas import ProviderQuotaExceeded, reserve_request
from yandex_client_modules.request_mixin import _usage_count, provider_request_headers


def test_anonymous_quota_request_fails_closed_by_default(monkeypatch):
    monkeypatch.delenv("LOGOS_QUOTA_REQUIRE_IDENTITY", raising=False)
    with pytest.raises(ProviderQuotaExceeded):
        reserve_request(None)


def test_anonymous_quota_request_is_allowed_only_when_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("LOGOS_QUOTA_REQUIRE_IDENTITY", "false")
    assert reserve_request(None) is None


def test_provider_headers_come_from_the_credential_not_the_shared_session():
    credential = SimpleNamespace(api_key="key-1", project_id="project-1")
    assert provider_request_headers(credential) == {
        "Authorization": "Api-Key key-1",
        "OpenAI-Project": "project-1",
    }


def test_malformed_token_counts_are_zero_not_errors():
    assert _usage_count({"input_tokens": "lots"}, "input_tokens") == 0
    assert _usage_count({"input_tokens": float("nan")}, "input_tokens") == 0
    assert _usage_count({"input_tokens": True}, "input_tokens") == 0
    assert _usage_count({"input_tokens": 12.9}, "input_tokens") == 12
    assert _usage_count({}, "total_tokens", 7) == 7


def test_login_classification_matches_whole_words_only():
    assert classify_login("github-copilot[bot]") == "copilot"
    assert classify_login("neologosx") == "human"
    assert classify_login("codexfan") == "human"
    assert classify_login("maksimp6") == "human"


def test_win_is_not_a_public_budget_operation():
    assert not hasattr(BudgetController, "record_win")


def test_board_journal_survives_reopen(tmp_path):
    path = tmp_path / "board.memory"
    with BoardStore(path) as board:
        thread = board.create_thread("Quality", "Notes", "discussion", "request-quality-1")
    with BoardStore(path) as reopened:
        titles = [item["title"] for item in reopened.threads()]
    assert thread["title"] == "Quality"
    assert "Quality" in titles


def test_concurrent_snapshots_never_see_a_half_updated_trace():
    trace = ExecutionTrace()
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer() -> None:
        index = 0
        while not stop.is_set():
            trace.add_event(f"event-{index}", {"index": index})
            trace.set_context(session_id=f"session-{index}")
            index += 1

    def reader() -> None:
        try:
            for _ in range(200):
                trace.make_snapshot()
        except BaseException as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(exc)

    writers = [threading.Thread(target=writer) for _ in range(2)]
    for thread in writers:
        thread.start()
    try:
        reader()
    finally:
        stop.set()
        for thread in writers:
            thread.join()
    assert errors == []


def test_emulator_refuses_loopback_pages_by_default():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = (
        "const { EmulatorSession, EmulatorError } = require('./browser/emulator/session');"
        "const s = new EmulatorSession({ fetch: async () => { throw new Error('reached'); } });"
        "s.navigate('http://127.0.0.1:9/').then(() => process.exit(3), (e) => {"
        "  console.log(e instanceof EmulatorError ? e.message : 'other:' + e); process.exit(0); });"
    )
    result = subprocess.run(
        [node, "-e", script],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("address is not public")
