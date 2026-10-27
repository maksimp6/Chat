"""Idea parser for Logos dialog exports.

Reads a dialog export (GigaChat deeplink JSON or plain markdown) and extracts
proposals: sentences by the OWNER that state an idea, wish, directive or
question-to-answer, plus recurring OFFERS by the assistant model. Outputs:

  ideas_report.md     human-readable digest with counts and quotes
  ideas_to_file.json  ready-to-post issue drafts {title, body, labels, source}

Usage:
  python scripts/idea_parser.py <input.json|input.md> [--out-dir DIR] [--source URL]

Categories (lexicon-driven, no external deps):
  proposal   - owner states an idea/directive
  question   - owner asks a personal/reflective question (kept, not filed)
  relay      - owner pasted agent output (not a proposal)
  offer      - assistant model repeats an offer; counted with repeats
  noise      - everything else
"""

import json
import re
import sys
from collections import Counter
from collections.abc import Generator
from pathlib import Path
from typing import Any

from core.wikifunctions import lowercase_string, trim_string

OFFER_MARKERS = [
    "если хочешь",
    "патчплейт",
    "карточки для",
    "карточка для",
    "ветку интерфейса",
    "первое сообщение",
    "доказательство выполнения",
    "могу выгрузить",
    "могу помочь",
    "могу встроить",
    "могу подготовить",
]
RELAY_MARKERS = [
    "pr #",
    "sha",
    "коммит",
    "head develop",
    "merge",
    "```",
    "ci ",
    "apk ",
    "gh-run",
]
PROPOSAL_HINTS = [
    "надо ",
    "нужно ",
    "сделай",
    "убрать",
    "удали",
    "замени",
    "создать",
    "хочу",
    "давай",
    "перестань",
    "запрет",
    "обязательно",
    "почему бы не",
]
QUESTION_HINTS = ["почему я", "почему когда", "почему алиса", "это эго", "чувствую", "чувству"]

OPENING_OFFER = re.compile(r"^если (нужно|хочешь|хотите)")
OPENING_ACTION = re.compile(r"^(как |что если |зачем |сделай|судите?|перечитай)")


def _owner_category(low: str, length: int) -> str:
    """Classify an OWNER sentence by its lowercase text."""
    if any(h in low for h in RELAY_MARKERS) and (length > 600 or "```" in low or "pr #" in low):
        return "relay"
    if any(m in low for m in RELAY_MARKERS):
        return "relay"
    if any(h in low for h in QUESTION_HINTS):
        return "question"
    if any(h in low for h in PROPOSAL_HINTS) or OPENING_ACTION.match(low):
        return "proposal"
    return "noise"


def _model_category(low: str) -> str:
    """Classify a MODEL sentence by its lowercase text."""
    if any(m in low for m in OFFER_MARKERS) or OPENING_OFFER.match(low):
        return "offer"
    return "noise"


def classify_sentence(role: str, text: str) -> str:
    """Return the category of a single dialogue sentence."""
    low = lowercase_string(text)
    if any(m in low for m in OFFER_MARKERS) or OPENING_OFFER.match(low):
        return "offer"
    if role != "OWNER":
        return _model_category(low)
    return _owner_category(low, len(text))


def split_sentences(text: str) -> list[str]:
    """Split a dialogue value into candidate sentences."""
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"'])", text)
    sentences = []
    for part in parts:
        stripped = trim_string(part)
        if 10 < len(stripped) < 600:
            sentences.append(stripped.strip("*`"))
    return sentences


def parse_deeplink_json(data: dict[str, Any]) -> Generator[tuple[str, str, str], None, None]:
    """Yield (role, timestamp, value) triples from a GigaChat deeplink export."""
    for m in data.get("chatMessages", []):
        role = "OWNER" if m.get("role") == "USER" else "MODEL"
        yield role, m.get("createdAt", ""), m.get("value", "")


def parse_markdown(text: str) -> Generator[tuple[str, str, str], None, None]:
    """Yield (role, timestamp, value) triples from a plain markdown dialogue log."""
    speaker_lines = {
        "владелец": "OWNER",
        "максим": "OWNER",
        "gigachat": "MODEL",
        "модель": "MODEL",
        "ассистент": "MODEL",
    }
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        stripped = trim_string(line)
        head_parts = stripped.split(":", 1)
        head = lowercase_string(trim_string(head_parts[0]))
        role = speaker_lines.get(head)
        if role:
            if current and buf:
                yield current, "", "\n".join(buf)
            current = role
            buf = [trim_string(head_parts[1])] if len(head_parts) > 1 else []
        elif current:
            buf.append(line)
    if current and buf:
        yield current, "", "\n".join(buf)


def _report_lines(
    proposals: list[dict[str, str]],
    offers: Counter[str],
    questions: list[dict[str, str]],
    src: str,
    name: str,
) -> list[str]:
    lines = [
        "# Idea parser report",
        f"Source: {src or name}",
        "",
        f"Proposals (candidate issues): {len(proposals)}",
        f"Recurring offers: {sum(offers.values())} hits across {len(offers)} markers",
        f"Personal questions (not filed): {len(questions)}",
        "",
        "## Proposals",
        "",
    ]
    for n, it in enumerate(proposals, 1):
        lines.append(f"{n}. [{it['ts']}] {it['quote']} (source: {it['source'] or name})")
    lines += ["", "## Offer counts", ""]
    for key, count in offers.most_common():
        lines.append(f"- `{key}`: {count} повторов")
    lines += ["", "## Questions (kept out of issues)", ""]
    lines += [f"- [{it['ts']}] {it['quote']}" for it in questions]
    return lines


def run_parser(input_path: Path, out_dir: Path, src: str) -> tuple[int, Counter[str], int]:
    """Parse the input file and write reports; return counters."""
    text = input_path.read_text(encoding="utf-8")
    if input_path.suffix == ".json":
        turns = parse_deeplink_json(json.loads(text))
    else:
        turns = parse_markdown(text)

    proposals: list[dict[str, str]] = []
    offers: Counter[str] = Counter()
    questions: list[dict[str, str]] = []
    for role, ts, value in turns:
        for sent in split_sentences(value):
            cat = classify_sentence(role, sent)
            if cat == "proposal":
                proposals.append({"role": role, "ts": ts, "quote": sent, "source": src})
            elif cat == "offer":
                key = next((m for m in OFFER_MARKERS if m in lowercase_string(sent)), "unknown")
                offers[key] += 1
            elif cat == "question":
                questions.append({"role": role, "ts": ts, "quote": sent, "source": src})

    out_dir.mkdir(parents=True, exist_ok=True)
    report = "\n".join(_report_lines(proposals, offers, questions, src, input_path.name))
    (out_dir / "ideas_report.md").write_text(report, encoding="utf-8")
    payload = {
        "generated": src or input_path.name,
        "proposals": proposals,
        "offers": dict(offers),
        "questions": questions,
    }
    (out_dir / "ideas_to_file.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(proposals), offers, len(questions)


def main() -> int:
    """CLI entry point."""
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    input_path = Path(args[0])
    out_dir = Path("reports")
    src = ""
    i = 1
    while i < len(args) - 1:
        if args[i] == "--out-dir":
            out_dir = Path(args[i + 1])
            i += 2
        elif args[i] == "--source":
            src = args[i + 1]
            i += 2
        else:
            i += 1
    n_prop, offers, n_q = run_parser(input_path, out_dir, src)
    print(
        f"OK: {n_prop} proposals, {sum(offers.values())} offer hits, {n_q} questions -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
