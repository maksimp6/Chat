"""Validate citation integrity; never infer guilt from a model's allegation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
REQUIRED = {"claim", "rule_quote", "path", "line", "source_quote", "reason", "reproduction"}


def added_lines(patch: str) -> dict[int, str]:
    """Map added lines to real new-file line numbers, across disjoint hunks."""
    result = {}
    line = None
    for text in patch.splitlines():
        hunk = HUNK.match(text)
        if hunk:
            line = int(hunk[1])
        elif line is not None and text.startswith("+"):
            result[line] = text[1:]
            line += 1
        elif line is not None and text.startswith(" "):
            line += 1
    return result


def validate_judgment(text: str, rules: str, files: list) -> dict:
    """Reject missing/false citations; valid citations still require merits review."""
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"findings", "limitations"}:
        raise ValueError("Expected findings and limitations, not a prose verdict")
    findings, limitations = data["findings"], data["limitations"]
    if not isinstance(findings, list) or not isinstance(limitations, list):
        raise TypeError("Findings and limitations must be lists")
    if len(findings) > 10 or not all(isinstance(x, str) for x in limitations):
        raise ValueError("Invalid finding count or limitations")
    sources = {file["filename"]: added_lines(file["patch"]) for file in files}
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != REQUIRED:
            raise ValueError("Each allegation needs a rule, source citation and reproduction")
        if any(
            not isinstance(finding[k], str) or not finding[k].strip() for k in REQUIRED - {"line"}
        ):
            raise ValueError("Empty allegation evidence")
        quote = finding["rule_quote"]
        if len(quote) < 24 or quote not in rules:
            raise ValueError("Invented or insufficiently specific rule quotation")
        line = finding["line"]
        if type(line) is not int or finding["path"] not in sources:
            raise ValueError("Invented source path or line")
        if sources[finding["path"]].get(line, "").strip() != finding["source_quote"].strip():
            raise ValueError("Source quote does not match the added line at claimed location")
    return {
        "status": "CANDIDATES_REQUIRE_REVIEW" if findings else "NO_CANDIDATES_NOT_APPROVAL",
        "citation_integrity": "CHECKED",
        "semantic_quality": "NOT_VERIFIED",
        "merge_approval": False,
        **data,
    }


def replay_rejected_report(source: Path, output: Path) -> int:
    """Replay the actual failed model output offline, with no credentials or network."""
    report = json.loads(source.read_text())
    try:
        validate_judgment(report["judgment"], "", [])
    except (TypeError, ValueError):
        result = {
            "status": "MODEL_OUTPUT_REJECTED",
            "source_run_id": report["run_id"],
            "source_head": report["head"],
            "validation": "Original uncited prose verdict refused",
            "paid_calls": 0,
            "merge_approval": False,
        }
        output.mkdir(exist_ok=True)
        (output / "replay.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 1
    raise ValueError("Negative control unexpectedly accepted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(replay_rejected_report(args.replay, args.output))
