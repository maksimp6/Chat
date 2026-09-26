"""Build a human-readable CI validation report from test/coverage outputs."""

from pathlib import Path
import json
import xml.etree.ElementTree as ET

FRONTEND_TESTS = [
    "test_file_manager.js",
    "test_dozzle_header.js",
    "test_frontend_boot.js",
    "test_project_tree.js",
    "test_header_dom.js",
    "test_frontend_smoke.js",
]

def _run_status(log: Path, marker: str) -> str:
    if not log.exists():
        return "not run"
    text = log.read_text(encoding="utf-8", errors="replace")
    return "passed" if marker in text else "failed or unavailable"

def _js_coverage() -> tuple[int, int]:
    total = covered = 0
    for path in Path("coverage-js").glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for result in payload.get("result", []):
            for fn in result.get("functions", []):
                total += 1
                if any(item.get("count", 0) for item in fn.get("ranges", [])):
                    covered += 1
    return covered, total

def main():
    out = Path("ci-validation-report.md")
    log = Path("frontend-test.log")
    lines = ["# Alice Pro validation report", "", "## Frontend tests", ""]
    markers = {
        "test_file_manager.js": "file_manager.js regression checks passed",
        "test_dozzle_header.js": "Dozzle header regression checks passed",
        "test_frontend_boot.js": "boot observability regression checks passed",
        "test_project_tree.js": "project tree regression checks passed",
        "test_header_dom.js": "header DOM regression checks passed",
        "test_frontend_smoke.js": "frontend smoke tests passed",
    }
    for test in FRONTEND_TESTS:
        lines.append("- `{}`: **{}**".format(test, _run_status(log, markers[test])))
    covered, total = _js_coverage()
    lines.append("- V8 JavaScript function coverage observed: **{}/{}**".format(covered, total) if total else "- V8 JavaScript coverage: **not available**")
    lines += ["", "## Backend coverage", ""]
    xml = Path("coverage.xml")
    if xml.exists():
        root = ET.parse(xml).getroot()
        lines.append("- Line coverage: **{:.2f}%**".format(float(root.attrib.get("line-rate", 0)) * 100))
        lines.append("- Branch coverage: **{:.2f}%**".format(float(root.attrib.get("branch-rate", 0)) * 100))
        lines.append("- Files measured: **{}**".format(len(root.findall(".//class"))))
    else:
        lines.append("- Coverage report was not produced.")
    lines += ["", "## Live Flask/browser resource validation", ""]
    lines.append("- Flask live log: " + ("available" if Path("flask-live.log").exists() else "not available"))
    lines.append("- Resource contract: real HTTP responses from the running Flask server.")
    lines.append("- Browser contract: Chromium network responses observed while exercising UI controls.")
    lines += ["", "## Result", "", "The CI status is authoritative. A failed test means the corresponding contract was not proven."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))

if __name__ == "__main__":
    main()
