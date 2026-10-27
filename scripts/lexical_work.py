"""Produce lexical work in the runner: parse labeled issues into idea reports.

The workflow (lexical-work.yml) runs this script on GitHub Actions. It finds
issues labeled `lexical-input`, runs the deterministic idea parser over their
bodies, and posts a compact report comment on each processed issue. No model
calls, no secrets beyond GITHUB_TOKEN, no network beyond the GitHub API.

Merge prerequisite: scripts/idea_parser.py from PR #1406 (feat/idea-parser).
The script fails with a clear message when the parser is not in the checkout.
"""

from __future__ import annotations
from core.env import env_get, env_require

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
REPO = env_get("GITHUB_REPOSITORY", "maksimp6/Chat")
LABEL = "lexical-input"
PARSER = Path("scripts/idea_parser.py")


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
            "User-Agent": "logos-lexical-work",
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
        chunk = gh("GET", f"{path}{'&' if '?' in path else '?'}page={page}&per_page=100")
        if not chunk:
            break
        out.extend(list(chunk))
        if len(chunk) < 100:
            break
        page += 1
    return out


def find_issues(explicit: str) -> list[dict[str, object]]:
    """Return the issues to process: one explicit number or all labeled."""
    if explicit.strip():
        issue = gh("GET", f"issues/{explicit.strip()}")
        return [issue]
    return gh_paginated(f"issues?labels={LABEL}&state=open")


def parse_body(text: str, out_dir: Path, source: str) -> str:
    """Run the deterministic parser over one text blob and return the report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "input.md"
    tmp.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(PARSER), str(tmp), "--out-dir", str(out_dir), "--source", source],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    report = out_dir / "ideas_report.md"
    if result.returncode != 0 or not report.exists():
        raise RuntimeError(f"parser failed: rc={result.returncode} {result.stderr[:200]}")
    return report.read_text(encoding="utf-8")


def comment_body(report: str, number: int, source: str) -> str:
    """Compose the issue comment with an explicit artifact pointer."""
    head = (
        f"## Лексическая работа (runner) · issue #{number}\n\n"
        f"Источник: {source or 'текст этого issue'}.\n\n"
        "Парсер детерминированный (без модели). Полный отчёт — в артефактах запуска.\n\n"
    )
    return head + report


def already_processed(number: int) -> bool:
    """True when this runner already posted a report on the issue."""
    comments = gh("GET", f"issues/{number}/comments?per_page=100")
    return any(
        "Лексическая работа (runner)" in str(c.get("body", ""))
        for c in comments
        if c.get("user", {}).get("login") == "github-actions[bot]"
    )


def main() -> int:
    """Entry point: iterate labeled issues, parse, comment, upload artifacts."""
    explicit = env_get("ISSUE_INPUT", "")
    source = env_get("SOURCE_URL", "")
    if not PARSER.exists():
        print(f"BLOCKER: {PARSER} not in checkout; merge PR #1406 first.")
        return 2
    out_dir = Path("lexical-output")
    out_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    for issue in find_issues(explicit):
        number = issue["number"]
        if already_processed(number):
            print(f"skip #{number}: already processed")
            continue
        body = str(issue.get("body") or "")
        if len(body) < 40:
            print(f"skip #{number}: body too short for lexical work")
            continue
        try:
            report = parse_body(body, out_dir, source)
        except RuntimeError as exc:
            print(f"FAIL #{number}: {exc}")
            continue
        gh("POST", f"issues/{number}/comments", {"body": comment_body(report, number, source)})
        (out_dir / f"issue-{number}.report.md").write_text(report, encoding="utf-8")
        processed += 1
        print(f"DONE #{number}")
    print(f"processed: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
