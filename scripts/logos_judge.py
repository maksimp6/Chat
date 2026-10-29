#!/usr/bin/env python3
"""«Суд Логоса»: приёмка отчёта исполнителя по правилам проекта.

Судит утверждения, а не людей. Вход — JSON-отчёт исполнителя. Каждое утверждение
проверяется по доказательству, которое можно перепроверить (git и файлы на диске),
а не по самоописанию. Выход: PASS или FAIL с перечнем нарушенных правил.

Правила:
  1. Атрибуция: author, source и executor заполнены.
  2. Доказательство: sha коммита есть в репозитории, файлы коммита совпадают с
     files_touched; файл артефакта существует.
  3. VERIFY RESULT: при ready=true нужен запуск приложения (started_app) и непустой
     лог запуска. «Готово» без запуска не принимается.
  4. Минимальный diff: files_touched не шире task_files.
  5. CI: ci_pass фиксируется как «задание завершено», не как «тесты пройдены»;
     вердикт от него не зависит.

Коды выхода: 0 — PASS, 1 — FAIL, 2 — отчёт не читается.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REQUIRED_ATTRIBUTION = ("author", "source", "executor")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def commit_files(repo: str, sha: str) -> set[str] | None:
    """Файлы, которые меняет коммит; None, если коммита нет в репозитории."""
    check = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
    )
    if check.returncode != 0:
        return None
    out = subprocess.run(
        ["git", "-C", repo, "show", "--name-only", "--pretty=format:", sha],
        capture_output=True,
        text=True,
    )
    return {line for line in out.stdout.splitlines() if line.strip()}


def judge(report: dict, repo: str) -> tuple[str, list[str], list[str]]:
    violations: list[str] = []
    notes: list[str] = []

    # 1. Атрибуция
    for field in REQUIRED_ATTRIBUTION:
        if not str(report.get(field) or "").strip():
            violations.append(f"атрибуция: не заполнено поле «{field}»")

    # 2. Доказательство
    artifact = report.get("artifact") or {}
    sha = str(artifact.get("sha") or "").strip().lower()
    path = str(artifact.get("path") or "").strip()
    if not sha and not path:
        violations.append("доказательство: нет артефакта (sha коммита или файл)")
    if sha:
        if not SHA_RE.match(sha):
            violations.append(f"доказательство: «{sha}» не похоже на sha коммита")
        else:
            in_repo = commit_files(repo, sha)
            if in_repo is None:
                violations.append(f"доказательство: коммит {sha} не найден в репозитории {repo}")
            else:
                claimed = set(report.get("files_touched") or [])
                if claimed and claimed != in_repo:
                    violations.append(
                        "доказательство: файлы коммита не совпадают с заявленными — "
                        f"лишние в заявке {sorted(claimed - in_repo)}, "
                        f"не заявлены {sorted(in_repo - claimed)}"
                    )
    if path and not os.path.exists(path):
        violations.append(f"доказательство: файл артефакта «{path}» не существует")

    # 3. VERIFY RESULT
    if report.get("ready") is True:
        if report.get("started_app") is not True:
            violations.append("VERIFY RESULT: «готово» заявлено без запуска приложения")
        log = str(report.get("log_path") or "")
        if not log or not os.path.isfile(log) or os.path.getsize(log) == 0:
            violations.append("VERIFY RESULT: нет непустого лога запуска")
    else:
        notes.append("статус не «готово»: приёмка результата не проводилась")

    # 4. Минимальный diff
    touched = set(report.get("files_touched") or [])
    scope = set(report.get("task_files") or [])
    if not scope:
        notes.append("минимальный diff: task_files не заполнены, объём задачи не проверен")
    elif touched - scope:
        violations.append(f"минимальный diff: файлы вне задачи {sorted(touched - scope)}")

    # 5. CI — фиксируется, вердикт не меняет
    ci = report.get("ci_pass")
    if ci is not None:
        status = "задание завершено" if ci else "задание не завершено"
        notes.append(f"CI: {status}; это не значит, что тесты пройдены")

    return ("FAIL" if violations else "PASS"), violations, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Приёмка отчёта исполнителя по правилам «Суд Логоса».")
    parser.add_argument("report", help="JSON-отчёт исполнителя")
    parser.add_argument("--repo", default=".", help="репозиторий, где проверяется sha")
    args = parser.parse_args(argv)
    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Отчёт не читается: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("Отчёт не читается: ожидается JSON-объект", file=sys.stderr)
        return 2
    verdict, violations, notes = judge(report, args.repo)
    print(f"ВЕРДИКТ: {verdict}")
    for item in violations:
        print(f"  ✗ {item}")
    for item in notes:
        print(f"  · {item}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
