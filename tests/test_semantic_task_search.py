"""All-state Issue search through the real entrypoint, without paid calls.

GitHub state/reason are retrieval metadata. A closed or completed Issue is not
automatically evidence that its requested product behaviour was delivered.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import semantic_pipeline as pipeline


UPDATED = "2026-10-20T10:00:00Z"
PREFIX = "Требование: журнал должен сохранять принятые события без потери."
SUFFIX = "Отдельное описание журнала находится после разделителя."


def issue(number, title, body, *, state="open", reason=None):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "state_reason": reason,
        "html_url": f"https://github.com/maksimp6/Chat/issues/{number}",
        "updated_at": UPDATED,
        "closed_at": UPDATED if state == "closed" else None,
        "labels": [],
        "comments": 0,
    }


@pytest.fixture
def pages(monkeypatch):
    """Provide open, completed, cancelled, duplicate and title-only Issues."""
    pull = issue(9105, "Журнал в PR", "Журнал в Pull Request не является отдельной задачей.")
    pull["pull_request"] = {"url": "https://api.github.com/repos/maksimp6/Chat/pulls/9105"}
    data = SimpleNamespace(
        values={
            1: [
                issue(9101, "Открытый журнал", PREFIX + "\n---\n" + SUFFIX),
                issue(
                    9102,
                    "Завершённый журнал",
                    "Реализация журнала объявлена завершённой.",
                    state="closed",
                    reason="completed",
                ),
                pull,
            ],
            2: [
                issue(
                    9103,
                    "Отменённый журнал",
                    "Разработка журнала отменена решением владельца.",
                    state="closed",
                    reason="not_planned",
                ),
                issue(
                    9104,
                    "Повторный журнал",
                    "Задача журнала закрыта как дубликат другой задачи.",
                    state="closed",
                    reason="duplicate",
                ),
                issue(9106, "Кристалл", None, state="closed", reason="completed"),
            ],
            3: [],
        },
        calls=[],
        model_calls=[],
        posts=[],
    )

    def get(path, token=None):
        query = parse_qs(urlsplit(path).query)
        data.calls.append(query)
        assert urlsplit(path).path == "/issues"
        assert query["per_page"] == ["100"]
        assert query["state"] == ["all"]
        page = int(query["page"][0])
        return deepcopy(data.values.get(page, []))

    def model(*args, **kwargs):
        data.model_calls.append((args, kwargs))
        pytest.fail("search mode must not initialize the model transport")

    def post(*args, **kwargs):
        data.posts.append((args, kwargs))
        pytest.fail("search without --publish-issue must remain read-only")

    monkeypatch.setattr(pipeline, "api_get", get)
    monkeypatch.setattr(pipeline, "yandex_call", model)
    monkeypatch.setattr(pipeline, "api_post", post)
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    return data


def card_from_output(output):
    opening = re.search(r"(?m)^(`{3,})json$", output)
    assert opening is not None
    text = output[opening.end() + 1 :].rsplit("\n" + opening.group(1), 1)[0]
    return json.loads(text)


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def test_task_corpus_uses_all_states_and_all_pages_without_label_filter(pages):
    corpus = pipeline.fetch_corpus(None, "tasks")
    assert {source["issue"] for source in corpus} == {9101, 9102, 9103, 9104, 9106}
    assert len(corpus) == 5
    assert [query["page"] for query in pages.calls] == [["1"], ["2"], ["3"]]
    for query in pages.calls:
        assert "labels" not in query
        assert query["sort"] == ["created"]
        assert query["direction"] == ["asc"]


def test_task_body_is_not_truncated_at_dialogue_separator(pages):
    source = next(s for s in pipeline.fetch_corpus(None, "tasks") if s["issue"] == 9101)
    assert PREFIX in source["text"]
    assert SUFFIX in source["text"]
    assert (
        source["body_sha256"] == hashlib.sha256((PREFIX + "\n---\n" + SUFFIX).encode()).hexdigest()
    )


def test_search_without_model_returns_closed_reasons_without_verifying_completion(pages, capsys):
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "журнала"]) == 0
    output = capsys.readouterr().out
    card = card_from_output(output)
    assert "RETRIEVAL_ONLY" in output
    assert card["model_called"] is False
    assert card["method"]["semantic"] == "NOT_PERFORMED"
    for number, title, reason in [
        (9102, "Завершённый журнал", "completed"),
        (9103, "Отменённый журнал", "not_planned"),
        (9104, "Повторный журнал", "duplicate"),
    ]:
        records = [r for r in dictionaries(card) if r.get("issue") == number]
        assert any(
            r.get("state") == "closed"
            and r.get("state_reason") == reason
            and r.get("title") == title
            for r in records
        )
    assert "VERIFIED: true" not in output
    assert pages.model_calls == []
    assert pages.posts == []


def test_short_title_only_closed_issue_is_searchable(pages, capsys):
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "Кристалл"]) == 0
    card = card_from_output(capsys.readouterr().out)
    evidence = card["evidence"]
    assert any(row["issue"] == 9106 and "Кристалл" in row["quote"] for row in evidence)
    assert card["model_called"] is False
    assert pages.model_calls == []
    assert pages.posts == []


@pytest.mark.parametrize(
    "query,field,original",
    [("события потери", "body", PREFIX + "\n---\n" + SUFFIX), ("Кристалл", "title", "Кристалл")],
)
def test_task_field_quotes_keep_exact_offsets_and_identity(query, field, original, pages, capsys):
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", query]) == 0
    card = card_from_output(capsys.readouterr().out)
    matches = [row for row in card["evidence"] if row["field"] == field]
    assert matches
    for row in matches:
        assert original[row["source_start"] : row["source_end"]] == row["quote"]
        assert row["sha256"] == hashlib.sha256(original.encode()).hexdigest()
        assert row["url"].startswith("https://github.com/maksimp6/Chat/issues/")
        assert row["updated_at"] == UPDATED
        assert row["retrieved_at"]


def test_task_search_can_find_body_before_separator(pages, capsys):
    assert (
        pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "события потери"]) == 0
    )
    card = card_from_output(capsys.readouterr().out)
    assert any(row["issue"] == 9101 and "события" in row["quote"] for row in card["evidence"])


def test_no_task_match_does_not_call_model_or_claim_no_work_exists(pages, capsys):
    assert (
        pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "морозильник клубника"])
        == 0
    )
    output = capsys.readouterr().out
    card = card_from_output(output)
    assert "NO_CANDIDATES" in output
    assert card["model_called"] is False
    assert card["method"]["semantic"] == "NOT_PERFORMED"
    assert pages.model_calls == []
    assert pages.posts == []


def test_duplicate_task_from_paginated_scan_is_not_silently_accepted(pages):
    pages.values[2].append(deepcopy(pages.values[1][0]))
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "журнал"]) != 0
    assert pages.model_calls == []
    assert pages.posts == []


def test_pull_request_body_does_not_consume_issue_corpus_budget(pages, capsys):
    pages.values[1][2]["body"] = "x" * (pipeline.MAX_CORPUS_CHARS + 1)
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "журнал"]) == 0
    card = card_from_output(capsys.readouterr().out)
    assert all(row.get("issue") != 9105 for row in dictionaries(card))
    assert pages.model_calls == []
    assert pages.posts == []


def test_malformed_task_page_fails_without_partial_report(pages, capsys):
    pages.values[2] = {"message": "rate limited"}
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "журнал"]) != 0
    captured = capsys.readouterr()
    assert "CONTRACT_VIOLATION" in captured.err
    assert "RETRIEVAL_ONLY" not in captured.out
    assert pages.model_calls == []
    assert pages.posts == []


@pytest.mark.parametrize("bad_row", [None, [], {"number": True}, {"number": 9110, "body": []}])
def test_malformed_task_row_is_sanitized_and_blocks_partial_results(bad_row, pages, capsys):
    pages.values[2].append(bad_row)
    assert pipeline.main(["--mode", "search", "--corpus", "tasks", "--query", "журнал"]) != 0
    captured = capsys.readouterr()
    assert "CONTRACT_VIOLATION" in captured.err
    assert "RETRIEVAL_ONLY" not in captured.out
    assert pages.model_calls == []
    assert pages.posts == []


def test_dialogue_corpus_also_includes_closed_labelled_sources(pages):
    corpus = pipeline.fetch_corpus(None, "dialogues")
    assert any(row["issue"] == 9102 for row in corpus)
    for query in pages.calls:
        assert query["labels"] == ["rag-source"]
        assert query["state"] == ["all"]


@pytest.mark.parametrize("bad_state", [[], {}, "pending"])
def test_malformed_dialogue_state_does_not_break_corpus_summary(bad_state, pages, capsys):
    pages.values[1][0]["state"] = bad_state
    assert pipeline.main(["--mode", "search", "--corpus", "dialogues", "--query", "журнал"]) != 0
    captured = capsys.readouterr()
    assert "CONTRACT_VIOLATION" in captured.err
    assert "RETRIEVAL_ONLY" not in captured.out


def test_search_defaults_to_tasks_and_ignores_model_allowance(pages, monkeypatch, capsys):
    monkeypatch.setenv("YANDEX_API_KEY", "present-but-unused")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "present-but-unused")
    assert pipeline.main(["--mode", "search", "--query", "Кристалл", "--allow-model-call"]) == 0
    assert all("labels" not in query for query in pages.calls)
    card = card_from_output(capsys.readouterr().out)
    assert card["model_called"] is False
    assert card["assessment"] == "NOT_ASSESSED"
    assert card["cost"] is None
    assert card["cost_scope"] == "total_execution_cost_not_measured"
    assert pages.model_calls == []


def test_summary_covers_unmatched_tasks_but_does_not_include_every_source(pages, capsys):
    assert pipeline.main(["--mode", "search", "--query", "Кристалл"]) == 0
    card = card_from_output(capsys.readouterr().out)
    assert card["corpus_sources_total"] == 5
    assert card["corpus_stats"] == {
        "total": 5,
        "open": 1,
        "closed": 4,
        "unknown": 0,
        "closed_reasons": {"completed": 2, "not_planned": 1, "duplicate": 1},
    }
    assert {source["issue"] for source in card["corpus_sources"]} == {9106}
    assert card["coverage"]["comments"] == "NOT_LOADED"
    assert card["coverage"]["linked_prs"] == "NOT_VERIFIED"
    assert card["coverage"]["completion"] == "NOT_ASSESSED"


def test_snapshot_identity_changes_when_closed_reason_changes(pages, capsys):
    assert pipeline.main(["--mode", "search", "--query", "Кристалл"]) == 0
    before = card_from_output(capsys.readouterr().out)
    pages.values[1][1]["state_reason"] = "not_planned"
    assert pipeline.main(["--mode", "search", "--query", "Кристалл"]) == 0
    after = card_from_output(capsys.readouterr().out)
    assert before["corpus_sha256"] != after["corpus_sha256"]
    assert after["corpus_stats"]["closed_reasons"]["not_planned"] == 2
