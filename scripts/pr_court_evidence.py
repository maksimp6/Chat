"""Validate citation integrity; never infer guilt from a model's allegation."""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

REQUIRED = {
    "claim",
    "rule_quote",
    "path",
    "line",
    "source_quote",
    "reason",
    "reproduction",
}
OPTIONAL = {"side", "severity", "consequence"}
SEVERITY_CRITERIA = {
    "P0": "Immediate loss of service, evidence, access control or data integrity",
    "P1": "Material failure of an intended function with a demonstrated affected scenario",
    "P2": "Limited defect with a bounded effect and a usable workaround",
}


def changed_lines(patch: str) -> dict[str, dict[int, str]]:
    """Map one GitHub file's complete hunks; reject malformed citation evidence."""
    result: dict[str, dict[int, str]] = {"LEFT": {}, "RIGHT": {}}
    if not patch:
        return result
    if not isinstance(patch, str) or not patch.startswith("@@ "):
        raise ValueError("Expected a per-file GitHub hunk fragment")
    # GitHub omits file headers. StringIO splits only at LF, retaining literal
    # CR and Unicode line separators in the source being quoted.
    wrapped = "--- a/source\n+++ b/source\n" + patch
    try:
        parsed = PatchSet(io.StringIO(wrapped, newline="\n"))
    except UnidiffParseError as exc:
        raise ValueError("Incomplete or malformed diff evidence") from exc
    if len(parsed) != 1 or not parsed[0]:
        raise ValueError("Expected hunks for exactly one source file")
    covered = set()
    for hunk in parsed[0]:
        for line in hunk:
            if line.diff_line_no is not None:
                covered.add(line.diff_line_no - 2)
            if line.is_added or line.is_removed:
                side = "RIGHT" if line.is_added else "LEFT"
                number = line.target_line_no if line.is_added else line.source_line_no
                if number in result[side]:
                    raise ValueError("Overlapping changed lines in diff evidence")
                result[side][number] = line.value.removesuffix("\n")
    header_count = 0
    for number, line in enumerate(io.StringIO(patch, newline="\n"), start=1):
        if line.startswith("@@ "):
            header_count += 1
        elif (
            number not in covered
            and line.removesuffix("\n") != "\\ No newline at end of file"
        ):
            # unidiff permits extra patch metadata outside hunks. A GitHub
            # fragment has none, so never accept ignored trailing source data.
            raise ValueError("Unexpected data outside diff hunks")
    if header_count != len(parsed[0]):
        raise ValueError("Malformed hunk header in diff evidence")
    return result


def added_lines(patch: str) -> dict[int, str]:
    """Compatibility helper for callers citing the new-file side."""
    return changed_lines(patch)["RIGHT"]


def validate_judgment(text: str, rules: str, files: list) -> dict:
    """Check line citations only; this function cannot establish a violation."""
    # Accept one complete Markdown code fence, never surrounding prose or partial JSON.
    fenced = re.fullmatch(
        r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text.strip(), re.DOTALL
    )
    data = json.loads(fenced[1] if fenced else text)
    if not isinstance(data, dict) or set(data) != {"findings", "limitations"}:
        raise ValueError("Expected findings and limitations, not a prose verdict")
    findings, limitations = data["findings"], data["limitations"]
    if not isinstance(findings, list) or not isinstance(limitations, list):
        raise TypeError("Findings and limitations must be lists")
    if not all(isinstance(x, str) for x in limitations):
        raise ValueError("Invalid limitations")
    sources = {}
    for file in files:
        changed = changed_lines(file.get("patch") or "")
        for side, path in (
            ("RIGHT", file["filename"]),
            ("LEFT", file.get("previous_filename", file["filename"])),
        ):
            key = (side, path)
            if key in sources:
                raise ValueError("Duplicate source path on the same diff side")
            sources[key] = changed[side]
    candidates = []
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not REQUIRED <= set(finding)
            or set(finding) - REQUIRED - OPTIONAL
        ):
            raise ValueError(
                "Each allegation needs a rule, source citation and reproduction"
            )
        if any(
            not isinstance(finding[k], str) or not finding[k].strip()
            for k in REQUIRED - {"line"}
        ):
            raise ValueError("Empty allegation evidence")
        quote = finding["rule_quote"]
        if len(quote) < 24 or quote not in rules:
            raise ValueError("Invented or insufficiently specific rule quotation")
        line = finding["line"]
        side = finding.get("side", "RIGHT")
        if not isinstance(side, str) or side not in {"LEFT", "RIGHT"}:
            raise ValueError("Source side must be LEFT (deleted) or RIGHT (added)")
        key = (side, finding["path"])
        if type(line) is not int or key not in sources:
            raise ValueError("Invented source path or line")
        if sources[key].get(line) != finding["source_quote"]:
            raise ValueError(
                "Source quote does not match the changed line at claimed location"
            )
        severity = finding.get("severity")
        consequence = finding.get("consequence")
        if "severity" in finding and (
            not isinstance(severity, str)
            or severity not in SEVERITY_CRITERIA
            or not isinstance(consequence, str)
            or not consequence.strip()
        ):
            raise ValueError(
                "Severity proposal needs P0/P1/P2 and a concrete consequence"
            )
        if "consequence" in finding and (
            not isinstance(consequence, str) or not consequence.strip()
        ):
            raise ValueError("Consequence must describe an affected scenario")
        candidate = {k: v for k, v in finding.items() if k != "severity"}
        candidate.update(
            side=side,
            status="ALLEGED",
            citation_integrity="CHECKED",
            context_sufficiency="NOT_VERIFIED",
            violation_proven=False,
            semantic_quality="NOT_VERIFIED",
            severity_proposal=severity or "UNASSESSED",
            severity_status="NOT_VERIFIED",
            independent_reviewer="UNASSIGNED",
        )
        candidates.append(candidate)
    return {
        "status": "CANDIDATES_REQUIRE_REVIEW"
        if findings
        else "NO_CANDIDATES_NOT_APPROVAL",
        "citation_integrity": "CHECKED",
        "context_sufficiency": "NOT_VERIFIED",
        "semantic_quality": "NOT_VERIFIED",
        "violation_proven": False,
        "merge_approval": False,
        "findings": candidates,
        "limitations": limitations,
        "coverage": {
            "automatic_check": "Added/deleted line quotations and supplied rule text only",
            "full_files": "Manual review requires pinned base/head file content",
            "cross_file_invariants": "Manual review requires all affected pinned sources and causal evidence",
            "finding_count_limit": None,
            "empty_findings": "No candidates returned; absence of defects is not established",
        },
        "confirmation_requires": [
            "Applicable rule and version at the time of the event",
            "Sufficient pinned context, including old/new files and related invariants",
            "Reproducible fact and causal link to the stated consequence",
            "Recorded objections and their disposition",
            "Independent reviewer identity and a separate reasoned decision",
        ],
        "severity_criteria": SEVERITY_CRITERIA,
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
