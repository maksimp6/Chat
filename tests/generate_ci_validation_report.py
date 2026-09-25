"""Build a human-readable CI validation report from test/coverage outputs."""

from pathlib import Path
import xml.etree.ElementTree as ET

def main():
    out = Path("ci-validation-report.md")
    lines = ["# Alice Pro validation report", "", "## Backend coverage", ""]
    xml = Path("coverage.xml")
    if xml.exists():
        root = ET.parse(xml).getroot()
        rate = float(root.attrib.get("line-rate", 0)) * 100
        branch = float(root.attrib.get("branch-rate", 0)) * 100
        lines.append(f"- Line coverage: **{rate:.2f}%**")
        lines.append(f"- Branch coverage: **{branch:.2f}%**")
        lines.append(f"- Files measured: **{len(root.findall(".//class"))}**")
    else:
        lines.append("- Coverage report was not produced.")
    lines += ["", "## Live Flask/browser resource validation", ""]
    live = Path("flask-live.log")
    lines.append("- Flask live log: " + ("available" if live.exists() else "not available"))
    lines.append("- Resource contract: real HTTP responses from the running Flask server.")
    lines.append("- Browser contract: Chromium network responses observed while exercising UI controls.")
    lines += ["", "## Result", "", "The CI status is authoritative. A failed test means the corresponding contract was not proven."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))

if __name__ == "__main__":
    main()