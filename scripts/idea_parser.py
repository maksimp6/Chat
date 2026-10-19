#!/usr/bin/env python3
"""Idea parser for Logos dialog exports.

Reads a dialog export (GigaChat deeplink JSON or plain markdown) and extracts
proposals: sentences by the OWNER that state an idea, wish, directive or
question-to-answer, plus recurring OFFERS by the assistant model. Outputs:

  ideas_report.md   human-readable digest with counts and quotes
  ideas_to_file.json  ready-to-post issue drafts {title, body, labels, source}

Usage:
  python scripts/idea_parser.py <input.json|input.md> [--out-dir DIR] [--source URL]

Categories (lexicon-driven, no external deps):
  proposal   - owner states an idea/directive ("надо", "нужно", "сделай", "почему бы не", ...)
  question   - owner asks a personal/reflective question (kept, not filed as issue)
  relay      - owner pasted agent output (likely long markdown/code blocks)
  offer      - assistant model repeats an offer ("Если хочешь, я могу", "патчплейт", ...)
  noise      - everything else
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

OFFER_MARKERS = [
    "если хочешь",
    "патчплейт",
    "карточки для",
    "карточка для",
    "ветку интерфейса",
    "первое сообщение",
    "доказательство выполнения",
]
PROPOSAL_HINTS = [
    "надо ", "нужно ", "сделай", "убрать", "удали", "замени", "создать", "создать",
    "хочу", "давай", "перестань", "запрет", "обязательно", "почему бы не",
]
QUESTION_HINTS = ["почему я", "почему когда", "почему алиса", "это эго", "чувствую", "чувству"]
RELAY_HINTS = ["```", "СДЕЛАНО", "## Доказательства", "PR #", "SHA", "HEAD "]

def classify_sentence(role: str, text: str) -> str:
    low = text.lower()
    if role == "MODEL":
        if any(m in low for m in OFFER_MARKERS):
            return "offer"
        return "noise"
    if any(h in low for h in RELAY_HINTS) and len(text) > 600:
        return "relay"
    if any(h in low for h in QUESTION_HINTS):
        return "question"
    if any(h in low for h in PROPOSAL_HINTS):
        return "proposal"
    if re.match(r"^(как |что если |зачем |сделай|судите?|перечитай)", low):
        return "proposal"
    return "noise"

def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"'])", text)
    return [p.strip().strip("*`") for p in parts if 10 < len(p.strip()) < 600]

def parse_deeplink_json(data: dict):
    for m in data.get("chatMessages", []):
        role = "OWNER" if m.get("role") == "USER" else "MODEL"
        yield role, m.get("createdAt", ""), m.get("value", "")

def parse_markdown(text: str):
    # Общий формат: строки "---" или заголовки не обязательны; ищем блоки диалога.
    # Правило: строки, начинающиеся с префикса имени говорящего.
    current = None
    buf = []
    ts = ""
    for line in text.splitlines():
        speaker = None
        low = line.strip().lower()
        if low.startswith(("владелец:", "максим:")):
            speaker = "OWNER"
        elif low.startswith(("gigachat:", "модель:", "ассистент:")):
            speaker = "MODEL"
        if speaker:
            if current and buf:
                yield current, ts, "\n".join(buf)
            current = speaker
            buf = [line.split(":", 1)[1].strip()] if ":" in line else []
            ts = ""
        elif current:
            buf.append(line)
    if current and buf:
        yield current, ts, "\n".join(buf)

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    path = Path(args[0])
    out_dir = Path("reports")
    src = ""
    i = 1
    while i < len(args):
        if args[i] == "--out-dir" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            src = args[i + 1]; i += 2
        else:
            i += 1

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        turn_iter = parse_deeplink_json(data)
    else:
        turn_iter = parse_markdown(text)

    ideas = []
    offers = Counter()
    questions = []
    for role, ts, val in turn_iter:
        for sent in split_sentences(val):
            cat = classify_sentence(role, sent)
            if cat == "proposal":
                ideas.append({"role": role, "ts": ts, "quote": sent, "source": src})
            elif cat == "offer":
                key = next((m for m in OFFER_MARKERS if m in sent.lower()), "unknown")
                offers[key] += 1
            elif cat == "question":
                questions.append({"role": role, "ts": ts, "quote": sent, "source": src})

    out_dir.mkdir(exist_ok=True)
    report = ["# Idea parser report", f"Source: {src or path.name}", "",
              f"Proposals (candidate issues): {len(ideas)}", f"Recurring offers: {sum(offers.values())} hits across {len(offers)} markers",
              f"Personal questions (not filed): {len(questions)}", "", "## Proposals", ""]
    for n, it in enumerate(ideas, 1):
        report.append(f"{n}. [{it['ts']}] {it['quote']} (source: {it['source'] or path.name})")
    report += ["", "## Offer counts", ""]
    for k, v in offers.most_common():
        report.append(f"- `{k}`: {v} повторов")
    report += ["", "## Questions (kept out of issues)", ""]
    for it in questions:
        report.append(f"- [{it['ts']}] {it['quote']}")
    (out_dir / "ideas_report.md").write_text("\n".join(report), encoding="utf-8")

    payload = {"generated": src or path.name, "proposals": ideas, "offers": dict(offers), "questions": questions}
    (out_dir / "ideas_to_file.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {len(ideas)} proposals, {sum(offers.values())} offer hits, {len(questions)} questions -> {out_dir}")

if __name__ == "__main__":
    main()
