#!/usr/bin/env python3
"""«Суд задачи бота»: судит доказательства, а не слова.

Вход — JSON-отчёт бота: task (acceptance, files), verdict_claimed, failure_reason,
run (command, exit_code, log_path), proofs (evidence), changed_files, claim_text,
self_assessment. Каждый критерий приёмки («готово = …») должен быть подтверждён
доказательством: текстом или файлом на диске. Самоописание не засчитывается.

Правила:
  1. Критерии: без непустого task.acceptance суд не выносится (код 2).
  2. Запуск: нужны command, exit_code 0 и лог запуска со значимыми словами.
     «Тесты пройдены» без лога не принимается.
  3. Самоощущение: маркеры («уверен», «по идее», «должно работать») в claim_text
     и self_assessment считаются отсутствием доказательства.
  4. Покрытие: критерий подтверждён, если доказательство содержит не меньше
     половины его основ слов (первые пять букв, без стоп-слов).
  5. Объём: changed_files не шире task.files, если task.files задан.
  6. Провал, заявленный самим ботом, — не выполнено.

Выход: по каждому критерию ВЫПОЛНЕНО или БЕСПОЛЕЗНЫЙ, затем ВЕРДИКТ.
Коды выхода: 0 — задача выполнена, 1 — не выполнена, 2 — судить нельзя.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "по",
    "с",
    "со",
    "к",
    "ко",
    "о",
    "об",
    "от",
    "до",
    "за",
    "из",
    "у",
    "а",
    "но",
    "или",
    "что",
    "как",
    "это",
    "не",
    "без",
    "для",
    "при",
    "же",
    "бы",
    "ли",
    "все",
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "for",
    "is",
    "and",
    "or",
    "with",
    "no",
}
SELF_MARKERS = (
    "уверен",
    "должно работать",
    "по идее",
    "наверное",
    "вроде работает",
    "скорее всего",
)
TEST_CLAIMS = ("тесты пройдены", "тесты прошли", "tests passed", "all tests pass")
MIN_LOG_WORDS = 5


def stems(text: str) -> set[str]:
    """Основы слов: первые пять букв, без стоп-слов и коротких слов."""
    words = re.findall(r"[a-zа-я0-9_]+", text.lower().replace("ё", "е"))
    return {word[:5] for word in words if len(word) > 2 and word not in STOPWORDS}


def proof_text(proof: dict) -> str:
    """Текст доказательства: строка, либо содержимое файла, если это путь."""
    evidence = str(proof.get("evidence") or "")
    if evidence and os.path.isfile(evidence):
        with open(evidence, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return evidence


def log_words(path: object) -> int:
    """Число значимых слов в логе запуска; 0, если лога нет."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        return len(re.findall(r"[a-zа-я0-9_]{3,}", handle.read().lower()))


def covered(criterion: str, proofs: list[dict]) -> str | None:
    """Первое доказательство, которое покрывает критерий, или None."""
    wanted = stems(criterion)
    if not wanted:
        return None
    needed = max(1, math.ceil(len(wanted) / 2))
    for proof in proofs:
        if len(wanted & stems(proof_text(proof))) >= needed:
            return str(proof.get("evidence") or "")
    return None


def judge(report: dict) -> tuple[int, list[str], list[str]]:
    """Возвращает код выхода, строки по критериям и причины провала."""
    task = report.get("task")
    if not isinstance(task, dict):
        task = {}
    run = report.get("run")
    if not isinstance(run, dict):
        run = {}
    criteria = [str(c) for c in (task.get("acceptance") or []) if str(c).strip()]
    if not criteria:
        return 2, [], ["нет критериев приёмки: без «готово = …» суд не выносится"]

    lines: list[str] = []
    reasons: list[str] = []
    proofs = [p for p in (report.get("proofs") or []) if isinstance(p, dict)]

    if report.get("verdict_claimed") == "failed":
        reasons.append(str(report.get("failure_reason") or "бот сам заявил провал"))
        return 1, lines, reasons

    if not run.get("command") or run.get("exit_code") is None:
        reasons.append("нет живого запуска: нет команды или exit_code")
    elif run.get("exit_code") != 0:
        reasons.append(f"exit_code {run.get('exit_code')}: запуск не прошёл")
    words = log_words(run.get("log_path"))
    if words < MIN_LOG_WORDS:
        reasons.append(
            f"лог запуска пустой или меньше {MIN_LOG_WORDS} значимых слов ({words})"
        )

    text = " ".join(
        str(report.get(key) or "") for key in ("claim_text", "self_assessment")
    ).lower()
    for marker in SELF_MARKERS:
        if marker in text:
            reasons.append(f"самоощущение вместо доказательства: «{marker}»")
    if any(claim in text for claim in TEST_CLAIMS) and words < MIN_LOG_WORDS:
        reasons.append("«тесты пройдены» без лога запуска: зелёный статус не приёмка")

    unproven = 0
    for criterion in criteria:
        evidence = covered(criterion, proofs)
        if evidence is None:
            unproven += 1
            lines.append(f"[БЕСПОЛЕЗНЫЙ] {criterion} — доказательства нет")
        else:
            lines.append(f"[ВЫПОЛНЕНО] {criterion} — доказательство: {evidence}")
    if unproven:
        reasons.append(f"не подтверждено критериев: {unproven} из {len(criteria)}")

    changed = set(report.get("changed_files") or [])
    scope = set(task.get("files") or [])
    if scope and changed - scope:
        reasons.append(f"файлы вне задания: {sorted(changed - scope)}")

    return (1 if reasons else 0), lines, reasons


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Использование: bot_task_verdict.py report.json", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"Отчёт не читается: {exc}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("Отчёт не читается: ожидается JSON-объект", file=sys.stderr)
        return 2

    code, lines, reasons = judge(report)
    if code == 2:
        print("ВЕРДИКТ: СУД НЕВОЗМОЖЕН")
        for reason in reasons:
            print(f"  ✗ {reason}")
        return 2
    for line in lines:
        print(line)
    for reason in reasons:
        print(f"  ✗ {reason}")
    if code == 0:
        print("ВЕРДИКТ: ЗАДАЧА ВЫПОЛНЕНА — доказательства закрыли критерии")
    elif report.get("verdict_claimed") == "failed":
        print("ВЕРДИКТ: БЕСПОЛЕЗНЫЙ — провал заявлен самим ботом")
    else:
        print("ВЕРДИКТ: БЕСПОЛЕЗНЫЙ")
    return code


if __name__ == "__main__":
    sys.exit(main())
