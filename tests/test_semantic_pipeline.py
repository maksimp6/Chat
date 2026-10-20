"""Offline publication-boundary controls for the semantic pipeline (#1413/#1435).

Only the external transports are replaced. Retrieval, model parsing, citation
validation, the publication decision and report rendering run through main().
These controls do not measure the behaviour of a live model.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import semantic_pipeline as pipeline


YANDEX_TRANSPORT = pipeline.yandex_call


FIRST = "Журнал сохраняет каждую подтверждённую запись на диск."
SECOND = "Резервная копия находится на другом сервере."
SOURCE = FIRST + " " + SECOND
SOURCE_BODY = "Задание источнику.\n---\n" + SOURCE
SOURCE_URL = "https://github.com/maksimp6/Chat/issues/9001"
UPDATED_AT = "2026-10-20T10:00:00Z"


def model_answer(**changes):
    """Return a complete model result, with per-test contract violations."""
    answer = {
        "answer": "Записи журнала сохраняются на диск.",
        "refusal": False,
        "citations": [{"id": 1, "quote": FIRST}],
    }
    answer.update(changes)
    return answer


@pytest.fixture
def transport(monkeypatch):
    """Supply deterministic Issue snapshots and capture paid/public side effects."""
    state = SimpleNamespace(
        issues=[
            {
                "number": 9001,
                "title": "Журнал",
                "body": SOURCE_BODY,
                "html_url": SOURCE_URL,
                "updated_at": UPDATED_AT,
            },
            {
                "number": 9002,
                "title": "Погода",
                "body": "Температура воздуха измеряется наружным датчиком.",
                "html_url": "https://github.com/maksimp6/Chat/issues/9002",
                "updated_at": UPDATED_AT,
            },
        ],
        model_text=json.dumps(model_answer(), ensure_ascii=False),
        provider_error=None,
        provider_exception=None,
        get_calls=[],
        model_calls=[],
        publications=[],
        publication_result={
            "id": 42,
            "html_url": "https://github.com/maksimp6/Chat/issues/9000#issuecomment-42",
        },
    )

    def api_get(path, token=None):
        state.get_calls.append((path, token))
        assert path.startswith("/issues?")
        assert "per_page=100" in path
        page = parse_qs(urlsplit(path).query)["page"]
        return state.issues if page == ["1"] else []

    def yandex_call(project, key, prompt):
        state.model_calls.append((project, key, prompt))
        if state.provider_exception is not None:
            raise state.provider_exception
        if state.provider_error is not None:
            return {"error": state.provider_error}
        return {
            "text": state.model_text,
            "usage": {"input_tokens": 120, "output_tokens": 40},
            "request_id": "offline-provider-response",
        }

    def api_post(path, payload, token):
        state.publications.append((path, payload, token))
        return state.publication_result

    monkeypatch.setattr(pipeline, "api_get", api_get)
    monkeypatch.setattr(pipeline, "yandex_call", yandex_call)
    monkeypatch.setattr(pipeline, "api_post", api_post)
    monkeypatch.setenv("GITHUB_TOKEN", "offline-github-token")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-yandex-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    return state


def run_cli(monkeypatch, *, mode="query", query="журнал", allow_model=True, extra=()):
    """Use the actual CLI entrypoint without replacing any pipeline decision."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "semantic_pipeline.py",
            "--mode",
            mode,
            "--query",
            query,
            "--publish-issue",
            "9000",
            *(["--allow-model-call"] if allow_model else []),
            *extra,
        ],
    )
    return pipeline.main()


def test_positive_control_publishes_once_with_source_provenance(transport, monkeypatch, capsys):
    assert run_cli(monkeypatch) == 0
    assert len(transport.model_calls) == 1
    assert len(transport.publications) == 1
    path, payload, token = transport.publications[0]
    assert path == "/issues/9000/comments"
    assert token == "offline-github-token"
    report = payload["body"]
    output = capsys.readouterr().out
    assert report in output
    assert transport.publication_result["html_url"] in output
    assert FIRST in report
    assert SOURCE_URL in report
    assert hashlib.sha256(SOURCE.encode()).hexdigest() in report
    assert UPDATED_AT in report
    assert "offset" in report.lower() or "start" in report.lower()
    assert "UNVERIFIED" in report or "INSUFFICIENT_EVIDENCE" in report


@pytest.mark.parametrize(
    "obj",
    [
        model_answer(citations=[{"id": 1, "quote": ""}]),
        model_answer(citations=[{"id": 1, "quote": " \t\n"}]),
        model_answer(citations=[{"id": True, "quote": FIRST}]),
        model_answer(citations=[{"id": "1", "quote": FIRST}]),
        model_answer(citations=[{"id": 9999, "quote": FIRST}]),
        model_answer(citations=[{"id": 1, "quote": SECOND}]),
        model_answer(citations=[{"id": 1, "quote": FIRST.replace("диск", "облако")}]),
        model_answer(citations=[]),
        model_answer(citations=None),
        model_answer(citations=[None]),
        model_answer(citations=[{"id": 1, "quote": 123}]),
        model_answer(refusal="false"),
        model_answer(refusal=True),
        model_answer(answer=123),
        model_answer(answer=""),
        {"answer": "Не хватает полей."},
        [],
        None,
    ],
    ids=[
        "empty-quote",
        "whitespace-quote",
        "boolean-id",
        "string-id",
        "unknown-id",
        "other-candidate-same-issue",
        "fabricated-quote",
        "no-evidence",
        "null-citations",
        "null-citation",
        "numeric-quote",
        "string-refusal",
        "contradictory-refusal",
        "numeric-answer",
        "empty-answer",
        "missing-fields",
        "array-root",
        "null-root",
    ],
)
def test_negative_control_blocks_publication(obj, transport, monkeypatch):
    transport.model_text = json.dumps(obj, ensure_ascii=False)
    assert run_cli(monkeypatch) != 0
    assert len(transport.model_calls) == 1
    assert transport.publications == []


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        "{",
        '{"answer": NaN}',
        "```json\n" + json.dumps(model_answer()) + "\n```",
        "prose before " + json.dumps(model_answer()),
        '{"answer":"a","answer":"b","refusal":true,"citations":[]}',
    ],
    ids=["plain-text", "truncated", "nan", "fenced", "prefixed", "duplicate-keys"],
)
def test_malformed_json_never_publishes(text, transport, monkeypatch):
    transport.model_text = text
    assert run_cli(monkeypatch) != 0
    assert transport.publications == []


def test_no_candidates_is_not_a_model_refusal_or_red_pass(transport, monkeypatch, capsys):
    assert run_cli(monkeypatch, mode="red", query="морозильник клубника") == 0
    assert transport.model_calls == []
    report = capsys.readouterr().out
    assert "NO_CANDIDATES" in report
    assert "NOT_RUN" in report
    assert "RED" in report
    assert "RED control: **PASS**" not in report


def test_model_refusal_records_actual_call_and_red_pass(transport, monkeypatch, capsys):
    transport.model_text = json.dumps(model_answer(answer="", refusal=True, citations=[]))
    assert run_cli(monkeypatch, mode="red") == 0
    assert len(transport.model_calls) == 1
    report = capsys.readouterr().out
    assert "MODEL_REFUSAL" in report.upper()
    assert "RED" in report and "PASS" in report
    assert len(transport.publications) == 1


def test_red_nonrefusal_is_blocked_even_with_a_verbatim_quote(transport, monkeypatch):
    assert run_cli(monkeypatch, mode="red") != 0
    assert len(transport.model_calls) == 1
    assert transport.publications == []


def test_provider_error_blocks_publication(transport, monkeypatch, capsys):
    transport.provider_error = "provider_http_429"
    assert run_cli(monkeypatch) != 0
    assert transport.publications == []
    report = capsys.readouterr()
    assert "429" in report.err or "PROVIDER_ERROR" in report.err


def test_provider_transport_exception_is_sanitized(transport, monkeypatch, capsys):
    transport.provider_exception = TimeoutError("secret-bearing-provider-error")
    assert run_cli(monkeypatch) != 0
    assert transport.publications == []
    report = capsys.readouterr()
    assert "secret-bearing-provider-error" not in report.out + report.err


def test_unrelated_verbatim_evidence_does_not_verify_the_claim(transport, monkeypatch):
    transport.model_text = json.dumps(
        model_answer(answer="На Redmi 9 подтверждена установка и работа приложения.")
    )
    assert run_cli(monkeypatch) == 0
    report = transport.publications[0][1]["body"]
    assert "UNVERIFIED" in report or "INSUFFICIENT_EVIDENCE" in report
    assert "VERIFIED: true" not in report


def test_empty_corpus_does_not_call_model(transport, monkeypatch, capsys):
    transport.issues = []
    assert run_cli(monkeypatch) == 0
    assert transport.model_calls == []
    assert "NO_CANDIDATES" in capsys.readouterr().out


def test_model_call_requires_explicit_allowance(transport, monkeypatch):
    assert run_cli(monkeypatch, allow_model=False) != 0
    assert transport.model_calls == []
    assert transport.publications == []


def test_no_candidates_needs_neither_allowance_nor_model_secrets(transport, monkeypatch, capsys):
    monkeypatch.delenv("YANDEX_API_KEY")
    monkeypatch.delenv("YANDEX_PROJECT_ID")
    assert run_cli(monkeypatch, query="морозильник клубника", allow_model=False) == 0
    assert transport.model_calls == []
    assert "NO_CANDIDATES" in capsys.readouterr().out


def test_selftest_is_offline(transport, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["semantic_pipeline.py", "--selftest"])
    assert pipeline.main() == 0
    assert transport.get_calls == []
    assert transport.model_calls == []
    assert transport.publications == []


def test_bm25_positive_control_and_no_matches():
    corpus = [
        {
            "issue": 9001,
            "text": SOURCE,
            "sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "source_url": SOURCE_URL,
            "updated_at": UPDATED_AT,
        }
    ]
    index = pipeline.BM25Index(pipeline.build_units(corpus))
    hits = index.search("журнал")
    assert hits and hits[0]["issue"] == 9001
    assert hits[0]["text"] == FIRST
    assert index.search("морозильник клубника") == []
    assert pipeline.BM25Index([]).search("журнал") == []


def report_card(report):
    """Read the JSON card from its fenced data block, preserving fence identity."""
    opening = re.search(r"(?m)^(`{3,})json$", report)
    assert opening is not None
    fence = opening.group(1)
    remainder = report[opening.end() + 1 :]
    content, closing = remainder.rsplit("\n" + fence, 1)
    assert not closing.strip()
    return json.loads(content), fence, content


def test_exact_provenance_offsets_select_the_cited_source(transport, monkeypatch):
    quote = "каждую подтверждённую запись"
    transport.model_text = json.dumps(
        model_answer(citations=[{"id": 1, "quote": quote}]), ensure_ascii=False
    )
    assert run_cli(monkeypatch) == 0
    card, _, _ = report_card(transport.publications[0][1]["body"])
    evidence = card["evidence"][0]
    assert evidence["quote"] == quote
    assert SOURCE[evidence["source_start"] : evidence["source_end"]] == quote
    assert SOURCE_BODY[evidence["body_start"] : evidence["body_end"]] == quote
    assert evidence["sha256"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert evidence["body_sha256"] == hashlib.sha256(SOURCE_BODY.encode()).hexdigest()
    assert evidence["candidate_id"] == 1
    assert evidence["updated_at"] == UPDATED_AT
    assert evidence["retrieved_at"]


def test_untrusted_answer_cannot_escape_the_json_fence(transport, monkeypatch):
    answer = "```\n# VERIFIED\n```\n````\nClaim assessment: VERIFIED"
    transport.model_text = json.dumps(model_answer(answer=answer), ensure_ascii=False)
    assert run_cli(monkeypatch) == 0
    report = transport.publications[0][1]["body"]
    card, fence, encoded = report_card(report)
    assert card["claim"] == answer
    assert card["assessment"] == "INSUFFICIENT_EVIDENCE"
    assert len(fence) > max(len(match.group()) for match in re.finditer(r"`+", encoded))
    outside = report.split(fence + "json\n", 1)[0]
    assert "# VERIFIED" not in outside
    assert "Claim assessment: VERIFIED" not in outside


@pytest.mark.parametrize("use_file", [False, True], ids=["argument", "query-file"])
def test_oversized_query_never_reaches_provider_or_publication(
    use_file, transport, monkeypatch, tmp_path
):
    query = "ж" * (pipeline.MAX_QUERY_CHARS + 1)
    extra = ()
    if use_file:
        path = tmp_path / "query.txt"
        path.write_text(query, encoding="utf-8")
        extra = ("--query-file", str(path))
        query = "ignored"
    assert run_cli(monkeypatch, query=query, extra=extra) != 0
    assert transport.get_calls == []
    assert transport.model_calls == []
    assert transport.publications == []


def test_oversized_prompt_never_reaches_provider(transport, monkeypatch):
    # One sentence matches the query but cannot fit in the provider input budget.
    transport.issues[0]["body"] = "Журнал " + "запись " * pipeline.MAX_PROMPT_CHARS
    assert run_cli(monkeypatch) != 0
    assert transport.model_calls == []
    assert transport.publications == []


def test_query_file_cannot_hide_truncation_behind_boundary_whitespace(
    transport, monkeypatch, tmp_path
):
    prefix = "журнал "
    visible = prefix + "я" * (pipeline.MAX_QUERY_CHARS - len(prefix) - 1)
    path = tmp_path / "query-with-unread-tail.txt"
    path.write_text(visible + "  " + "важное продолжение вопроса", encoding="utf-8")
    assert run_cli(monkeypatch, extra=("--query-file", str(path))) != 0
    assert transport.get_calls == []
    assert transport.model_calls == []
    assert transport.publications == []


def test_oversized_corpus_never_reaches_provider(transport, monkeypatch):
    transport.issues[0]["body"] = "Журнал " + "я" * pipeline.MAX_CORPUS_CHARS
    assert run_cli(monkeypatch) != 0
    assert transport.model_calls == []
    assert transport.publications == []


def test_publication_requires_token_before_work(transport, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    assert run_cli(monkeypatch) != 0
    assert transport.get_calls == []
    assert transport.model_calls == []
    assert transport.publications == []
    assert "CONTRACT_VIOLATION" in capsys.readouterr().err


def test_provider_declared_error_is_not_retried(transport, monkeypatch):
    transport.provider_error = "provider_http_503"
    assert run_cli(monkeypatch) != 0
    assert len(transport.model_calls) == 1
    assert transport.publications == []


def test_publication_transport_error_is_not_retried(transport, monkeypatch, capsys):
    def failing_post(path, payload, token):
        transport.publications.append((path, payload, token))
        raise TimeoutError("secret-bearing-publication-error")

    monkeypatch.setattr(pipeline, "api_post", failing_post)
    assert run_cli(monkeypatch) != 0
    assert len(transport.model_calls) == 1
    assert len(transport.publications) == 1
    captured = capsys.readouterr()
    assert "secret-bearing-publication-error" not in captured.out + captured.err


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        [],
        {},
        {
            "id": True,
            "html_url": "https://github.com/maksimp6/Chat/issues/9000#issuecomment-True",
        },
        {
            "id": 42,
            "html_url": "https://github.com/maksimp6/Chat/issues/9999#issuecomment-42",
        },
        {
            "id": 42,
            "html_url": "https://github.com/maksimp6/Chat/issues/9000#issuecomment-99",
        },
        {
            "id": 42,
            "html_url": "https://github.com.attacker.invalid/maksimp6/Chat/issues/9000#issuecomment-42",
        },
    ],
    ids=["null", "array", "empty", "boolean-id", "wrong-issue", "wrong-id", "wrong-host"],
)
def test_invalid_publication_receipt_is_uncertain_without_retry(
    receipt, transport, monkeypatch, capsys
):
    transport.publication_result = receipt
    assert run_cli(monkeypatch) == 4
    assert len(transport.model_calls) == 1
    assert len(transport.publications) == 1
    captured = capsys.readouterr()
    assert "PUBLICATION_UNCERTAIN" in captured.err


def provider_envelope(*, text=None, usage=None):
    """A complete provider envelope with only the requested test delta."""
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(model_answer()) if text is None else text,
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 120, "output_tokens": 40} if usage is None else usage,
    }


@pytest.mark.parametrize(
    "envelope",
    [
        [],
        None,
        {"status": "completed", "output": None},
        {"status": "completed", "output": [{"type": "message", "content": None}]},
        provider_envelope(text={"secret": "secret-provider-envelope"}),
        provider_envelope(text=123),
        provider_envelope(usage=["secret-provider-envelope"]),
        provider_envelope(usage="secret-provider-envelope"),
        provider_envelope(usage=[]),
        provider_envelope(usage=""),
        provider_envelope(usage=0),
        provider_envelope(usage=False),
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        None,
                        {"type": "output_text", "text": json.dumps(model_answer())},
                    ],
                }
            ],
            "usage": {},
        },
    ],
    ids=[
        "array-root",
        "null-root",
        "null-output",
        "null-content",
        "object-text",
        "numeric-text",
        "array-usage",
        "string-usage",
        "empty-array-usage",
        "empty-string-usage",
        "zero-usage",
        "boolean-usage",
        "mixed-malformed-content",
    ],
)
def test_malformed_provider_envelope_is_fail_closed(envelope, transport, monkeypatch, capsys):
    requests = []
    closed = []
    raw = json.dumps(envelope).encode()

    class Response:
        status = 200

        def read(self, limit):
            return raw[:limit]

    class Connection:
        def __init__(self, host, timeout):
            assert host == pipeline.HOST

        def request(self, method, path, body, headers):
            requests.append((method, path))

        def getresponse(self):
            return Response()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(pipeline, "yandex_call", YANDEX_TRANSPORT)
    monkeypatch.setattr(pipeline.http.client, "HTTPSConnection", Connection)
    assert run_cli(monkeypatch) != 0
    assert requests == [("POST", "/v1/responses")]
    assert closed == [True]
    assert transport.publications == []
    captured = capsys.readouterr()
    assert "secret-provider-envelope" not in captured.out + captured.err


def test_actual_provider_transport_preserves_identity_and_bounds(transport, monkeypatch, capsys):
    envelope = provider_envelope()
    envelope["id"] = "response-123"
    raw = json.dumps(envelope).encode()
    requests = []
    closed = []

    class Response:
        status = 200

        def read(self, limit):
            assert limit == pipeline.MAX_RESPONSE_BYTES + 1
            return raw[:limit]

    class Connection:
        def __init__(self, host, timeout):
            assert host == pipeline.HOST
            assert timeout == 90

        def request(self, method, path, body, headers):
            requests.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return Response()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(pipeline, "yandex_call", YANDEX_TRANSPORT)
    monkeypatch.setattr(pipeline.http.client, "HTTPSConnection", Connection)
    assert run_cli(monkeypatch) == 0
    assert len(requests) == 1
    method, path, payload, headers = requests[0]
    assert (method, path) == ("POST", "/v1/responses")
    assert payload["store"] is False
    assert payload["background"] is False
    assert payload["max_output_tokens"] == pipeline.MAX_OUTPUT_TOKENS
    assert json.loads(payload["input"])["query"] == "журнал"
    assert headers["Authorization"] == "Api-Key offline-yandex-key"
    assert closed == [True]
    assert len(transport.publications) == 1
    report = transport.publications[0][1]["body"]
    card, _, _ = report_card(report)
    assert card["provider_request_id"] == "response-123"
    assert card["usage"] == {"input_tokens": 120, "output_tokens": 40}
    assert "offline-yandex-key" not in report
    assert "offline-github-token" not in report
    output = capsys.readouterr()
    assert "offline-yandex-key" not in output.out + output.err
    assert "offline-github-token" not in output.out + output.err
