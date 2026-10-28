"""File a Logos Court case for every error (AGENTS.md, «Ошибки и дела Суда»).

A case is a GitHub issue titled ``Суд Логоса: <error>`` with the label
``from-dialog``, carrying the episode card of the Court procedure
(``docs/agents/logos-court-procedure.md``) and a link to the canonical case
#1413. The same error (same code and message) goes to one open case: a repeat
is added to it as a comment instead of opening a second case.

The repository is public, so the issue carries no dialog text: the full error
(partial answer included) is age-encrypted and committed to the LFS branch
``data/court-evidence``; the card links it with its SHA-256.

Each month has its own key (``<LOGOS_COURT_KEYS_DIR>/YYYY-MM.agekey``, made on
first use). A month's key is published on the same branch, ``keys/YYYY-MM.agekey``,
once the following month is over: every piece of evidence stays sealed for at
least a month and then becomes readable by everyone.

``LOGOS_COURT_TOKEN`` (or ``GITHUB_TOKEN`` / ``GH_TOKEN``) authorizes the
GitHub calls; ``LOGOS_COURT_REPO`` names the repository.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "case_body",
    "case_title",
    "file_case",
    "file_case_now",
    "fingerprint",
    "publish_due_keys",
    "store_evidence",
]

log = logging.getLogger("logos.court.filing")

API = "https://api.github.com"
LABEL = "from-dialog"
CANONICAL_CASE = 1413
MARKER = "logos-court-auto"
EVIDENCE_BRANCH = "data/court-evidence"
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
    key = json.dumps(
        [str(error.get("code") or ""), _message(report)], ensure_ascii=False
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _iso(timestamp: Any) -> str:
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    return "UNKNOWN"


def _q(value: Any) -> str:
    """A YAML scalar that survives any text: JSON strings are valid YAML."""
    return json.dumps(
        "UNKNOWN" if value in (None, "") else str(value), ensure_ascii=False
    )


# --- encrypted LFS evidence (same steps as scripts/redmi_publish_diagnostics.sh)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _keys_dir() -> Path:
    return Path(
        os.environ.get("LOGOS_COURT_KEYS_DIR")
        or Path.home() / ".config" / "logos" / "court-keys"
    )


def _month(when: datetime) -> str:
    return when.strftime("%Y-%m")


def _month_after(month: str, months: int) -> datetime:
    year, number = (int(part) for part in month.split("-"))
    index = year * 12 + number - 1 + months
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=UTC)


def _monthly_recipient(now: datetime) -> str:
    """The public key of this month's key, creating the key on first use."""
    keys = _keys_dir()
    keys.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = keys / f"{_month(now)}.agekey"
    if not key.exists():
        subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
        key.chmod(0o600)
    return subprocess.run(
        ["age-keygen", "-y", str(key)], check=True, capture_output=True, text=True
    ).stdout.strip()


def _due_keys(now: datetime) -> list[Path]:
    """Keys of months that ended at least a month ago and are not published yet."""
    keys = _keys_dir()
    if not keys.is_dir():
        return []
    return [
        key
        for key in sorted(keys.glob("????-??.agekey"))
        if _month_after(key.stem, 2) <= now
        and not key.with_name(key.name + ".published").exists()
    ]


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def _publish(files: list[tuple[Path, str]], token: str, message: str) -> dict[str, str]:
    """Commit files to the evidence branch; prove every .age file from GitHub.

    ``*.age`` goes through LFS; anything else (a published key) is plain git."""
    repo = _repo()
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_1": "AUTHORIZATION: basic " + header,
    }
    remote = f"https://github.com/{repo}.git"
    branch = EVIDENCE_BRANCH
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        _git(["init", "-q"], work, env)
        _git(["remote", "add", "origin", remote], work, env)
        heads = subprocess.run(
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                "refs/heads/" + branch,
            ],
            cwd=work,
            env=env,
            capture_output=True,
        )
        if heads.returncode == 0:
            _git(["fetch", "--quiet", "origin", "refs/heads/" + branch], work, env)
            _git(["checkout", "-q", "-b", branch, "FETCH_HEAD"], work, env)
        elif heads.returncode == 2:
            _git(["checkout", "-q", "--orphan", branch], work, env)
        else:
            raise RuntimeError(f"git ls-remote exited {heads.returncode}")
        _git(["config", "user.name", "Logos Court"], work, env)
        _git(
            ["config", "user.email", "logos-court@users.noreply.github.com"], work, env
        )
        _git(["lfs", "install", "--local"], work, env)
        (work / ".gitattributes").write_text(
            "*.age filter=lfs diff=lfs merge=lfs -text\n"
        )
        sealed = {}
        for source, dest in files:
            target = work / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if dest.endswith(".age"):
                sealed[dest] = _sha256_file(source)
        _git(["add", "--", ".gitattributes", *(dest for _, dest in files)], work, env)
        for dest, sha in sealed.items():
            if f"oid sha256:{sha}" not in _git(["show", ":" + dest], work, env):
                raise RuntimeError(f"LFS pointer of {dest} does not carry its digest")
        _git(["commit", "-q", "-m", message], work, env)
        commit = _git(["rev-parse", "HEAD"], work, env).strip()
        _git(["push", "origin", "HEAD:refs/heads/" + branch], work, env)
        if sealed:
            # Read it back from GitHub into a fresh store: a local object is no upload proof.
            verify = Path(tmp) / "verify"
            _git(
                [
                    "clone",
                    "-q",
                    "--no-checkout",
                    "--single-branch",
                    "--branch",
                    branch,
                    remote,
                    str(verify),
                ],
                Path(tmp),
                env,
            )
            _git(["lfs", "install", "--local"], verify, env)
            _git(["checkout", "-q", "--detach", commit], verify, env)
            for dest, sha in sealed.items():
                _git(
                    [
                        "lfs",
                        "fetch",
                        "--include=" + dest,
                        "--exclude=",
                        "origin",
                        commit,
                    ],
                    verify,
                    env,
                )
                _git(["lfs", "checkout", dest], verify, env)
                if _sha256_file(verify / dest) != sha:
                    raise RuntimeError(f"{dest} read back from GitHub differs")
    dest = files[0][1]
    return {
        "status": "remote_verified",
        "url": f"https://github.com/{repo}/blob/{commit}/{dest}",
        "commit": commit,
        "path": dest,
        "sha256": sealed.get(dest) or _sha256_file(files[0][0]),
    }


def publish_due_keys(token: str, now: datetime | None = None) -> list[str]:
    """Publish every month key whose month ended at least a month ago."""
    now = now or datetime.now(UTC)
    published = []
    for key in _due_keys(now):
        receipt = _publish(
            [(key, f"keys/{key.name}")], token, f"Publish court key {key.stem}"
        )
        key.with_name(key.name + ".published").write_text(json.dumps(receipt) + "\n")
        log.info("court key %s published: %s", key.stem, receipt["url"])
        published.append(receipt["url"])
    return published


def store_evidence(report: dict[str, Any], token: str) -> dict[str, str]:
    """The full error, encrypted to this month's key, in LFS; never plaintext."""
    now = datetime.now(UTC)
    try:
        recipient = _monthly_recipient(now)
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "evidence.json"
            plain.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            sealed = Path(tmp) / "evidence.json.age"
            subprocess.run(
                ["age", "-r", recipient, "-o", str(sealed), str(plain)], check=True
            )
            dest = (
                f"court/{_month(now)}/{fingerprint(report)}/"
                f"{report.get('invocation_id') or 'unknown'}.json.age"
            )
            receipt = _publish([(sealed, dest)], token, "Court evidence " + dest)
            receipt["key"] = f"keys/{_month(now)}.agekey"
            receipt["key_published_from"] = (
                _month_after(_month(now), 2).date().isoformat()
            )
            return receipt
    except Exception as exc:
        log.exception("court evidence not stored: %s", _message(report))
        return {"status": "NOT_STORED", "reason": f"{type(exc).__name__}: {exc}"}


# --- case text ---------------------------------------------------------------


def case_title(report: dict[str, Any]) -> str:
    title = "Суд Логоса: " + _message(report)
    if len(title) > GITHUB_TITLE_CHARS:
        title = title[: GITHUB_TITLE_CHARS - 1] + "…"
    return title


def _evidence_source(evidence: dict[str, str]) -> str:
    if evidence.get("status") == "remote_verified":
        return f"улика (age, LFS) {evidence['url']} sha256 {evidence['sha256']}"
    return f"улика не сохранена: {evidence.get('reason') or 'UNKNOWN'}"


def _occurrence(report: dict[str, Any], now: datetime, evidence: dict[str, str]) -> str:
    error = _error_fields(report)
    sources = [
        f"invocation {report.get('invocation_id') or 'UNKNOWN'}",
        f"conversation {report.get('conversation_id') or 'UNKNOWN'}",
        f"session {report.get('session_id') or 'UNKNOWN'}",
        f"trace {report.get('trace_id') or 'UNKNOWN'}",
        _evidence_source(evidence),
    ]
    provider = {
        key: error[key]
        for key in ("provider_status", "provider_reason", "provider_response_id")
        if error.get(key) is not None
    }
    if provider:
        sources.append(
            "provider " + json.dumps(provider, ensure_ascii=False, default=str)
        )
    fact = f"Ход завершился ошибкой: {_message(report)}"
    if error.get("partial_output"):
        fact += ". Частичный ответ есть, он только в зашифрованной улике"
    return "\n".join(
        [
            f"recorded_at: {_q(now.isoformat())}",
            f"event_at: {_q(_iso(report.get('created_at')))}",
            "sources: [" + ", ".join(_q(s) for s in sources) + "]",
            f"claim: {_q(_message(report))}",
            f"fact: {_q(fact)}",
        ]
    )


def _fit(text: str) -> str:
    if len(text) <= GITHUB_BODY_CHARS:
        return text
    note = "\n\n[Обрезано: лимит GitHub на длину текста]"
    return text[: GITHUB_BODY_CHARS - len(note)] + note


def case_body(
    report: dict[str, Any], evidence: dict[str, str], now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    fp = fingerprint(report)
    repo = _repo()
    card = "\n".join(
        [
            f"episode: {_q(f'{CANONICAL_CASE}/auto-{fp}/1')}",
            f"task: {_q(f'https://github.com/{repo}/issues/{CANONICAL_CASE}')}",
            _occurrence(report, now, evidence),
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
        f"Повторы той же ошибки добавляются сюда комментариями. "
        f"Текст диалога здесь не публикуется: полная ошибка — в зашифрованной улике.\n\n"
        f"```yaml\n{card}\n```\n"
    )


def _comment_body(
    report: dict[str, Any], evidence: dict[str, str], now: datetime
) -> str:
    return _fit(
        f"Повтор ошибки ({MARKER}:{fingerprint(report)}).\n\n"
        f"```yaml\n{_occurrence(report, now, evidence)}\n```\n"
    )


# --- GitHub ------------------------------------------------------------------


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


def _find_open_case(fp: str, token: str) -> int | None:
    with _known_lock:
        if fp in _known_cases:
            return _known_cases[fp]
    query = f'repo:{_repo()} is:issue is:open in:body "{MARKER}:{fp}"'
    found = _request("GET", "/search/issues?q=" + urllib.parse.quote(query), token)
    items = (found or {}).get("items") or []
    return items[0]["number"] if items else None


def file_case_now(report: dict[str, Any]) -> str | None:
    """Open the case, or add the repeat to the open one; return its URL."""
    token = _token()
    if not token:
        log.warning(
            "court case not filed, no LOGOS_COURT_TOKEN/GITHUB_TOKEN: %s",
            _message(report),
        )
        return None
    now = datetime.now(UTC)
    fp = fingerprint(report)
    repo = _repo()
    evidence = store_evidence(report, token)
    try:
        publish_due_keys(token, now)
    except Exception:
        log.exception("court keys due for publication were not published")
    number = _find_open_case(fp, token)
    if number is not None:
        _request(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            token,
            {"body": _comment_body(report, evidence, now)},
        )
    else:
        issue = _request(
            "POST",
            f"/repos/{repo}/issues",
            token,
            {
                "title": case_title(report),
                "body": case_body(report, evidence, now),
                "labels": [LABEL],
            },
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
