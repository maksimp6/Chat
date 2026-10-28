"""File a Logos Court case for every error (AGENTS.md, «Ошибки и дела Суда»).

A case is a GitHub issue titled ``Суд Логоса: <error>`` with the label
``from-dialog``, carrying the episode card of the Court procedure
(``docs/agents/logos-court-procedure.md``) and a link to the canonical case
#1413. The same error (same code and message) goes to one open case: a repeat
is added to it as a comment instead of opening a second case.

``LOGOS_COURT_TOKEN`` (or ``GITHUB_TOKEN`` / ``GH_TOKEN``) authorizes the
GitHub calls; ``LOGOS_COURT_REPO`` names the repository.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

__all__ = ["case_body", "case_title", "file_case", "file_case_now", "fingerprint"]

log = logging.getLogger("logos.court.filing")

API = "https://api.github.com"
LABEL = "from-dialog"
CANONICAL_CASE = 1413
MARKER = "logos-court-auto"
# GitHub rejects titles over 256 and bodies over 65536 characters.
GITHUB_TITLE_CHARS = 256
GITHUB_BODY_CHARS = 65536

_known_cases: dict[str, int] = {}
_known_lock = threading.Lock()


def _repo() -> str:
    return os.environ.get("LOGOS_COURT_REPO", "maksimp6/Chat")


def _token() -> str:
    return (
        os.environ.get("LOGOS_COURT_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    )


def _error_fields(report: dict[str, Any]) -> dict[str, Any]:
    error = report.get("error")
    return error if isinstance(error, dict) else {"message": error}


def _message(report: dict[str, Any]) -> str:
    error = _error_fields(report)
    message = error.get("message") or error.get("error") or error.get("code")
    if not message:
        return "UNKNOWN"
    return str(message).strip().splitlines()[0]


def fingerprint(report: dict[str, Any]) -> str:
    """One open case per distinct error: code and message, not the invocation."""
    error = _error_fields(report)
    key = json.dumps([str(error.get("code") or ""), _message(report)], ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _iso(timestamp: Any) -> str:
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    return "UNKNOWN"


def _q(value: Any) -> str:
    """A YAML scalar that survives any text: JSON strings are valid YAML."""
    return json.dumps("UNKNOWN" if value in (None, "") else str(value), ensure_ascii=False)


def case_title(report: dict[str, Any]) -> str:
    title = "Суд Логоса: " + _message(report)
    if len(title) > GITHUB_TITLE_CHARS:
        title = title[: GITHUB_TITLE_CHARS - 1] + "…"
    return title


def _occurrence(report: dict[str, Any], now: datetime) -> str:
    error = _error_fields(report)
    sources = [
        f"invocation {report.get('invocation_id') or 'UNKNOWN'}",
        f"conversation {report.get('conversation_id') or 'UNKNOWN'}",
        f"session {report.get('session_id') or 'UNKNOWN'}",
        f"trace {report.get('trace_id') or 'UNKNOWN'}",
    ]
    provider = {
        key: error[key]
        for key in ("provider_status", "provider_reason", "provider_response_id")
        if error.get(key) is not None
    }
    if provider:
        sources.append("provider " + json.dumps(provider, ensure_ascii=False, default=str))
    partial = error.get("partial_output")
    fact = f"Ход завершился ошибкой: {_message(report)}"
    if partial:
        fact += f". Частичный ответ: {partial}"
    return "\n".join(
        [
            f"recorded_at: {_q(now.isoformat())}",
            f"event_at: {_q(_iso(report.get('created_at')))}",
            "sources: [" + ", ".join(_q(s) for s in sources) + "]",
            f"claim: {_q(_message(report))}",
            f"fact: {_q(fact)}",
        ]
    )


def _raw(report: dict[str, Any]) -> str:
    raw = json.dumps(report.get("error"), ensure_ascii=False, indent=2, default=str)
    return f"<details><summary>Ошибка, как она сохранена</summary>\n\n```json\n{raw}\n```\n\n</details>"


def _fit(text: str) -> str:
    if len(text) <= GITHUB_BODY_CHARS:
        return text
    note = "\n\n[Обрезано: лимит GitHub на длину текста]"
    return text[: GITHUB_BODY_CHARS - len(note)] + note


def case_body(report: dict[str, Any], now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    fp = fingerprint(report)
    repo = _repo()
    card = "\n".join(
        [
            f"episode: {_q(f'{CANONICAL_CASE}/auto-{fp}/1')}",
            f"task: {_q(f'https://github.com/{repo}/issues/{CANONICAL_CASE}')}",
            _occurrence(report, now),
            "owner: UNASSIGNED",
            f"rule_at_event: {_q('AGENTS.md, «Ошибки и дела Суда»')}",
            f"current_rule: {_q('AGENTS.md, «Ошибки и дела Суда»')}",
            "severity: UNKNOWN",
            f"consequence: {_q('Пользователь получил ⚠️ Ошибка вместо ответа')}",
            "citation_integrity: NOT_VERIFIED",
            "context_sufficiency: NOT_VERIFIED",
            "causal_link: UNKNOWN",
            "objections: UNKNOWN",
            "decision: ALLEGED",
            f"decision_reason: {_q('Дело подано автоматически при ошибке; решения владельца нет')}",
            "decision_by: UNASSIGNED",
            "fix: NOT_DONE",
            "verification: NOT_RUN",
            "conflict_of_interest: UNKNOWN",
            "owner_acceptance: PENDING",
            "next_checkpoint: NOT_SCHEDULED",
            f"checkpoint_reason: {_q('Ожидает рассмотрения владельцем')}",
            "closure: OPEN",
            "history: []",
        ]
    )
    return _fit(
        f"<!-- {MARKER}:{fp} -->\n"
        f"Дело подано автоматически: ход Логоса завершился ошибкой. "
        f"Каноническое дело: #{CANONICAL_CASE}. "
        f"Повторы той же ошибки добавляются сюда комментариями.\n\n"
        f"```yaml\n{card}\n```\n\n{_raw(report)}\n"
    )


def _comment_body(report: dict[str, Any], now: datetime) -> str:
    return _fit(
        f"Повтор ошибки ({MARKER}:{fingerprint(report)}).\n\n"
        f"```yaml\n{_occurrence(report, now)}\n```\n\n{_raw(report)}\n"
    )


def _request(method: str, path: str, token: str, payload: Any = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")


def _find_open_case(fp: str, token: str) -> Optional[int]:
    with _known_lock:
        if fp in _known_cases:
            return _known_cases[fp]
    query = f'repo:{_repo()} is:issue is:open in:body "{MARKER}:{fp}"'
    found = _request("GET", "/search/issues?q=" + urllib.parse.quote(query), token)
    items = (found or {}).get("items") or []
    return items[0]["number"] if items else None


def file_case_now(report: dict[str, Any]) -> Optional[str]:
    """Open the case, or add the repeat to the open one; return its URL."""
    token = _token()
    if not token:
        log.warning(
            "court case not filed, no LOGOS_COURT_TOKEN/GITHUB_TOKEN: %s", _message(report)
        )
        return None
    now = datetime.now(timezone.utc)
    fp = fingerprint(report)
    repo = _repo()
    number = _find_open_case(fp, token)
    if number is not None:
        _request(
            "POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": _comment_body(report, now)}
        )
    else:
        issue = _request(
            "POST",
            f"/repos/{repo}/issues",
            token,
            {"title": case_title(report), "body": case_body(report, now), "labels": [LABEL]},
        )
        number = issue["number"]
    with _known_lock:
        _known_cases[fp] = number
    url = f"https://github.com/{repo}/issues/{number}"
    log.info("court case %s for: %s", url, _message(report))
    return url


def _file_logged(report: dict[str, Any]) -> None:
    try:
        file_case_now(report)
    except Exception:
        log.exception("court case filing failed: %s", _message(report))


def file_case(report: dict[str, Any]) -> None:
    """File in the background: a failing turn never waits for GitHub."""
    threading.Thread(
        target=_file_logged, args=(report,), name="logos-court-filing", daemon=True
    ).start()
