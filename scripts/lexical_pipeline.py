"""Full-cycle lexical pipeline: dialog chunks -> verified aggregated report.

Owner verdict 2026-10-20: two processed chunks do not prove full-dialog coverage.
This module implements the required cycle: every chunk of one dialog is processed,
concatenation is verified against the declared source sha256, chunk results are
aggregated with cross-chunk cancellations, and a single report is posted with
separate counters for chunks, issues, proposals and offers.

Dialog issue format (label `lexical-dialog`):
    <!-- lexical-dialog
    source: <provenance>
    content_sha256: <sha256 of the full source, utf-8>
    chunks: #1411, #1412
    -->

Chunk bodies must concatenate (in the declared order, no separators) to exactly
the source document. Mismatch = FAIL, no report is generalized.
"""

from core.env import env_get, env_require

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

API = "https://api.github.com"
REPO = env_get("GITHUB_REPOSITORY", "maksimp6/Chat")
LABEL = "lexical-dialog"
DIALOG_RE = re.compile(r"<!--\s*lexical-dialog\s*\n(.*?)-->", re.DOTALL)
PARSER = Path("scripts/idea_parser.py")
CANCEL_MARKERS = (
    "не надо",
    "отмена",
    "отменяем",
    "отмени",
    "забудь",
    "не делай",
    "не нужно",
)
# Speaker labels of dialog chunks, as in scripts/idea_parser.py parse_markdown.
OWNER_LABELS = frozenset(["владелец", "максим"])
MODEL_LABELS = frozenset(["gigachat", "модель", "ассистент"])
STOP_WORDS = frozenset(
    [
        "и",
        "в",
        "на",
        "с",
        "а",
        "но",
        "что",
        "как",
        "это",
        "для",
        "не",
        "по",
        "из",
        "у",
        "же",
        "за",
        "то",
        "их",
        "мы",
        "вы",
        "я",
        "он",
        "она",
        "они",
        "мой",
        "твой",
        "свой",
        "было",
        "была",
        "будет",
        "есть",
        "был",
        "быть",
        "чтобы",
        "если",
        "тогда",
        "когда",
        "которая",
        "которого",
        "которые",
    ]
)


def gh(method: str, path: str, body: object | None = None) -> object:
    """Call the GitHub API with the workflow token."""
    token = env_require("GH_TOKEN")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}/repos/{REPO}/{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "logos-lexical-pipeline",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def gh_paginated(path: str) -> list[dict[str, object]]:
    """Fetch all pages: GitHub caps per_page at 100, so walk pages until empty."""
    out: list[dict[str, object]] = []
    page = 1
    while True:
        chunk = gh(
            "GET", f"{path}{'&' if '?' in path else '?'}page={page}&per_page=100"
        )
        if not chunk:
            break
        out.extend(list(chunk))
        if len(chunk) < 100:
            break
        page += 1
    return out


def parse_meta(body: str) -> dict[str, str]:
    """Parse the lexical-dialog manifest block from an issue body."""
    match = DIALOG_RE.search(body)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def chunk_texts(chunk_numbers: list[int]) -> list[str]:
    """Fetch chunk bodies in declared order; the payload starts after '---'."""
    texts = []
    for number in chunk_numbers:
        issue = gh("GET", f"issues/{number}")
        body = str(issue.get("body") or "")
        if "---" in body:
            body = body.split("---", 1)[1]
        # The issue preamble ends with a fixed "\n\n" before the payload;
        # remove exactly that separator so the payload keeps its own bytes
        # (a chunk may legitimately start with newlines — do not strip more).
        body = body.removeprefix("\n\n")
        texts.append(body)
    return texts


def verify_full_coverage(texts: list[str], declared: str) -> dict[str, object]:
    """Verify that concatenated chunks reproduce the declared source hash."""
    digest = hashlib.sha256("".join(texts).encode("utf-8")).hexdigest()
    return {
        "full_coverage": digest == declared,
        "computed_sha256": digest,
        "declared_sha256": declared,
    }


def meaningful_words(text: str) -> set[str]:
    """Lowercase words of >=5 letters that are not stop words."""
    return {w for w in re.findall(r"[а-яё]{5,}", text.lower()) if w not in STOP_WORDS}


def cancelled_later(proposals: list[dict[str, str]], later_text: str) -> list[str]:
    """Return quotes of proposals contradicted by a later owner message."""
    marks: list[str] = []
    for p in proposals:
        quote_words = meaningful_words(p["quote"])
        for sent in re.split(r"(?<=[.!?…])\s+", later_text):
            if not any(m in sent.lower() for m in CANCEL_MARKERS):
                continue
            if len(quote_words & meaningful_words(sent)) >= 2:
                marks.append(p["quote"])
                break
    return marks


def owner_replies(texts: list[str]) -> list[str]:
    """Owner replies of each chunk; a speaker turn may continue into the next chunk."""
    owner = False
    out: list[str] = []
    for text in texts:
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            head = stripped.split(":", 1)[0].strip().lower()
            if head in OWNER_LABELS or head in MODEL_LABELS:
                owner = head in OWNER_LABELS
                if owner and ":" in stripped:
                    kept.append(stripped.split(":", 1)[1].strip())
            elif owner:
                kept.append(line)
        out.append("\n".join(kept))
    return out


def main() -> int:
    """Full cycle: fetch dialog issues, verify coverage, aggregate, post report."""
    if not PARSER.exists():
        print(f"BLOCKER: {PARSER} not in checkout.")
        return 2
    out_dir = Path("lexical-output")
    out_dir.mkdir(parents=True, exist_ok=True)
    issues = gh_paginated(f"issues?labels={LABEL}&state=open")
    dialogs = [i for i in issues if not i.get("pull_request")]
    processed = 0
    failed = 0
    for dialog in dialogs:
        number = dialog["number"]
        meta = parse_meta(str(dialog.get("body") or ""))
        declared = meta.get("content_sha256", "")
        chunk_numbers = [int(x) for x in re.findall(r"\d+", meta.get("chunks", ""))]
        if not declared or not chunk_numbers:
            print(f"FAIL #{number}: incomplete manifest")
            failed += 1
            continue
        texts = chunk_texts(chunk_numbers)
        coverage = verify_full_coverage(texts, declared)
        if not coverage["full_coverage"]:
            gh(
                "POST",
                f"issues/{number}/comments",
                {
                    "body": (
                        "## Полный цикл — FAIL\n\n"
                        f"Конкатенация чанков не совпадает с исходником по sha256: "
                        f"`{coverage['computed_sha256'][:16]}…` против `{declared[:16]}…`. "
                        "Отчёт не публикуется: полнота не доказана."
                    )
                },
            )
            print(f"FAIL #{number}: coverage mismatch")
            failed += 1
            continue
        proposals: list[dict[str, str]] = []
        chunk_proposals: list[list[dict[str, str]]] = []
        offers: dict[str, int] = {}
        questions_total = 0
        failed_chunks: list[int] = []
        for chunk_number, text in zip(chunk_numbers, texts):
            tmp = out_dir / f"input-{chunk_number}.md"
            tmp.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PARSER),
                    str(tmp),
                    "--out-dir",
                    str(out_dir),
                    "--source",
                    f"chunk #{chunk_number}",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            payload_file = out_dir / "ideas_to_file.json"
            if result.returncode != 0 or not payload_file.exists():
                print(
                    f"FAIL #{number} chunk #{chunk_number}: rc={result.returncode} {result.stderr[:200]}"
                )
                failed_chunks.append(chunk_number)
                continue
            try:
                payload = json.loads(payload_file.read_text(encoding="utf-8"))
                chunk_props = list(payload["proposals"])
                chunk_offers = {
                    key: int(count) for key, count in payload["offers"].items()
                }
                chunk_questions = len(payload["questions"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"FAIL #{number} chunk #{chunk_number}: bad payload: {exc}")
                failed_chunks.append(chunk_number)
                continue
            payload_file.unlink()
            chunk_proposals.append(chunk_props)
            proposals.extend(chunk_props)
            for key, count in chunk_offers.items():
                offers[key] = offers.get(key, 0) + count
            questions_total += chunk_questions
        if failed_chunks:
            gh(
                "POST",
                f"issues/{number}/comments",
                {
                    "body": (
                        "## Полный цикл — FAIL\n\n"
                        f"Разбор чанков не удался: {', '.join('#' + str(n) for n in failed_chunks)}. "
                        "Отчёт не публикуется: полнота не доказана."
                    )
                },
            )
            print(f"FAIL #{number}: chunk parse failed")
            failed += 1
            continue
        owner_texts = owner_replies(texts)
        cancelled: list[str] = []
        for k, own in enumerate(chunk_proposals):
            cancelled += cancelled_later(own, "\n\n".join(owner_texts[k + 1 :]))
        final = [p for p in proposals if p["quote"] not in cancelled]
        lines = [
            "# Полный цикл лексического разбора диалога",
            f"Issue: #{number} · chunks: {', '.join('#' + str(n) for n in chunk_numbers)}",
            f"content_sha256: `{coverage['computed_sha256']}`",
            "full_coverage: yes",
            "",
            "## Счётчики",
            f"- chunks_processed: {len(chunk_proposals)}",
            f"- issues (чанков): {len(chunk_proposals)}",
            f"- proposals_total: {len(proposals)}",
            f"- proposals_final: {len(final)} (после межчанковых отмен)",
            f"- offers_total: {sum(offers.values())} hits across {len(offers)} markers",
            f"- questions_total: {questions_total}",
            "",
            "## Proposals (final)",
            "",
        ]
        for n, p in enumerate(final, 1):
            lines.append(f"{n}. {p['quote']}")
        if cancelled:
            lines += ["", "## Отменено поздними сообщениями владельца", ""]
            lines += [f"- {c}" for c in cancelled]
        report = "\n".join(lines)
        gh("POST", f"issues/{number}/comments", {"body": report})
        (out_dir / f"dialog-{number}.report.md").write_text(report, encoding="utf-8")
        processed += 1
        print(f"DONE #{number}")
    print(f"processed dialogs: {processed}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
