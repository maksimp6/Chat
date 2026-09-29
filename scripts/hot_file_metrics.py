#!/usr/bin/env python3
"""Report high-churn source files as modularity-review candidates."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Iterable


SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".sh",
    ".kt",
    ".java",
    ".go",
    ".rs",
}
EXCLUDED_PREFIXES = (
    "tests/",
    "docs/",
    ".github/",
    "android/app/build/",
    "build/",
    "dist/",
    "vendor/",
    "node_modules/",
)
EXCLUDED_NAMES = {
    "package-lock.json",
    "coverage.xml",
    "coverage.json",
}


@dataclass
class FileHistory:
    touches: int = 0
    additions: int = 0
    deletions: int = 0
    authors: set[str] = field(default_factory=set)
    newest_commit_index: int | None = None

    @property
    def churn(self) -> int:
        return self.additions + self.deletions


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _is_source_candidate(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if Path(normalized).name in EXCLUDED_NAMES:
        return False
    return Path(normalized).suffix.lower() in SOURCE_SUFFIXES


def _parse_history(raw: str) -> tuple[dict[str, FileHistory], int]:
    histories: dict[str, FileHistory] = defaultdict(FileHistory)
    commit_index = -1
    author = ""
    touched_this_commit: set[str] = set()

    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("@@@"):
            commit_index += 1
            touched_this_commit = set()
            parts = line[3:].split("\t", 2)
            author = parts[1] if len(parts) > 1 else ""
            continue

        parts = line.split("\t")
        if len(parts) != 3:
            continue
        additions_raw, deletions_raw, path = parts
        if not _is_source_candidate(path):
            continue
        if additions_raw == "-" or deletions_raw == "-":
            continue

        entry = histories[path]
        entry.additions += int(additions_raw)
        entry.deletions += int(deletions_raw)
        if author:
            entry.authors.add(author)
        if path not in touched_this_commit:
            entry.touches += 1
            touched_this_commit.add(path)
        if entry.newest_commit_index is None:
            entry.newest_commit_index = max(commit_index, 0)

    return dict(histories), max(commit_index + 1, 0)


def _normalize(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return min(1.0, value / maximum)


def analyze_repository(repo_root: Path, *, commits: int = 200) -> dict:
    repo_root = repo_root.resolve()
    raw = _git(
        repo_root,
        "log",
        f"-n{max(1, int(commits))}",
        "--numstat",
        "--format=@@@%H%x09%an%x09%ct",
        "--",
    )
    histories, commit_count = _parse_history(raw)

    rows = []
    for path, history in histories.items():
        absolute = repo_root / path
        if not absolute.is_file():
            continue
        try:
            loc = len(absolute.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            continue
        churn_per_loc = history.churn / max(loc, 100)
        if commit_count <= 1 or history.newest_commit_index is None:
            recency = 1.0
        else:
            recency = max(
                0.0,
                1.0 - (history.newest_commit_index / (commit_count - 1)),
            )
        rows.append(
            {
                "path": path,
                "touches": history.touches,
                "additions": history.additions,
                "deletions": history.deletions,
                "churn": history.churn,
                "authors": len(history.authors),
                "loc": loc,
                "churn_per_loc": round(churn_per_loc, 4),
                "touch_density": round(history.touches / max(commit_count, 1), 4),
                "recency": round(recency, 4),
            }
        )

    maxima = {
        "touches": max((row["touches"] for row in rows), default=0),
        "churn": max((row["churn"] for row in rows), default=0),
        "authors": max((row["authors"] for row in rows), default=0),
        "churn_per_loc": max((row["churn_per_loc"] for row in rows), default=0.0),
    }

    for row in rows:
        components = {
            "touches": _normalize(row["touches"], maxima["touches"]),
            "churn": _normalize(row["churn"], maxima["churn"]),
            "churn_per_loc": _normalize(row["churn_per_loc"], maxima["churn_per_loc"]),
            "authors": _normalize(row["authors"], maxima["authors"]),
            "recency": row["recency"],
        }
        score = 100 * (
            0.30 * components["touches"]
            + 0.30 * components["churn"]
            + 0.20 * components["churn_per_loc"]
            + 0.10 * components["authors"]
            + 0.10 * components["recency"]
        )
        row["score"] = round(score, 2)
        if row["loc"] >= 300:
            candidate_kind = "split_candidate"
        elif row["touches"] >= 5:
            candidate_kind = "extract_shared_logic"
        else:
            candidate_kind = "watch"
        row["candidate_kind"] = candidate_kind
        row["score_components"] = {key: round(value, 4) for key, value in components.items()}

    rows.sort(key=lambda row: (-row["score"], row["path"]))
    return {
        "commit_window": max(1, int(commits)),
        "commits_observed": commit_count,
        "weights": {
            "touches": 0.30,
            "churn": 0.30,
            "churn_per_loc": 0.20,
            "authors": 0.10,
            "recency": 0.10,
        },
        "files": rows,
    }


def _append_table(lines: list[str], rows: list[dict]) -> None:
    lines.extend(
        [
            "| Rank | File | Score | Touches | Churn | LOC | Churn/LOC | Authors |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {rank} | `{path}` | {score:.2f} | {touches} | {churn} | "
            "{loc} | {ratio:.2f} | {authors} |".format(
                rank=index,
                path=row["path"],
                score=row["score"],
                touches=row["touches"],
                churn=row["churn"],
                loc=row["loc"],
                ratio=row["churn_per_loc"],
                authors=row["authors"],
            )
        )


def render_markdown(report: dict, *, top: int = 20) -> str:
    limit = max(1, int(top))
    split_rows = [row for row in report["files"] if row["candidate_kind"] == "split_candidate"][
        :limit
    ]
    shared_rows = [
        row for row in report["files"] if row["candidate_kind"] == "extract_shared_logic"
    ][: min(limit, 10)]

    lines = [
        "# Hot-file modularity report",
        "",
        (
            f"History window: {report['commit_window']} commits; "
            f"observed: {report['commits_observed']}."
        ),
        "",
        "High churn is a review signal, not proof that a file must be split.",
        "Churn/LOC uses a 100-line denominator floor so recently collapsed stubs do not dominate.",
        "",
        "## Module split candidates (current LOC >= 300)",
        "",
    ]
    _append_table(lines, split_rows)
    lines.extend(
        [
            "",
            "## Small hot files / shared-logic candidates",
            "",
            "These files are too small to justify splitting by size; repeated touches may instead suggest shared logic, configuration extraction, or a forwarding stub.",
            "",
        ]
    )
    _append_table(lines, shared_rows)
    lines.extend(
        [
            "",
            "## Score weights",
            "",
            "- touches: 30%",
            "- line churn: 30%",
            "- churn / current LOC (100-line floor): 20%",
            "- unique authors: 10%",
            "- recency: 10%",
            "",
        ]
    )
    return "\n".join(lines)


def _write(path: str | None, content: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--commits", type=int, default=200)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json-out")
    parser.add_argument("--markdown-out")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = analyze_repository(Path(args.repo_root), commits=args.commits)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown = render_markdown(report, top=args.top)

    _write(args.json_out, json_text)
    _write(args.markdown_out, markdown)
    if not args.json_out and not args.markdown_out:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
