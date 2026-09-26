import json
import xml.etree.ElementTree as ET

from tests import generate_ci_validation_report as report


def test_run_status_handles_missing_passed_and_failed_logs(tmp_path):
    log = tmp_path / "frontend.log"
    assert report._run_status(log, "marker") == "not run"

    log.write_text("prefix marker suffix", encoding="utf-8")
    assert report._run_status(log, "marker") == "passed"
    assert report._run_status(log, "absent") == "failed or unavailable"


def test_js_coverage_counts_functions_and_ignores_invalid_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    coverage_dir = tmp_path / "coverage-js"
    coverage_dir.mkdir()
    (coverage_dir / "good.json").write_text(
        json.dumps(
            {
                "result": [
                    {
                        "functions": [
                            {"ranges": [{"count": 1}]},
                            {"ranges": [{"count": 0}]},
                            {"ranges": []},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (coverage_dir / "bad.json").write_text("{bad", encoding="utf-8")

    assert report._js_coverage() == (1, 3)


def _write_coverage_xml(path):
    root = ET.Element("coverage", {"line-rate": "0.75", "branch-rate": "0.5"})
    packages = ET.SubElement(root, "packages")
    package = ET.SubElement(packages, "package")
    classes = ET.SubElement(package, "classes")
    ET.SubElement(classes, "class", {"name": "one"})
    ET.SubElement(classes, "class", {"name": "two"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_validation_report_main_renders_frontend_python_and_live_sections(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)

    markers = {
        "file_manager.js regression checks passed",
        "Dozzle header regression checks passed",
        "boot observability regression checks passed",
        "project tree regression checks passed",
        "header DOM regression checks passed",
        "frontend smoke tests passed",
    }
    (tmp_path / "frontend-test.log").write_text("\n".join(sorted(markers)), encoding="utf-8")

    coverage_dir = tmp_path / "coverage-js"
    coverage_dir.mkdir()
    (coverage_dir / "v8.json").write_text(
        json.dumps({"result": [{"functions": [{"ranges": [{"count": 2}]}]}]}),
        encoding="utf-8",
    )
    _write_coverage_xml(tmp_path / "coverage.xml")
    (tmp_path / "flask-live.log").write_text("running", encoding="utf-8")

    report.main()

    rendered = (tmp_path / "ci-validation-report.md").read_text(encoding="utf-8")
    assert "test_header_dom.js`: **passed**" in rendered
    assert "V8 JavaScript function coverage observed: **1/1**" in rendered
    assert "Line coverage: **75.00%**" in rendered
    assert "Branch coverage: **50.00%**" in rendered
    assert "Files measured: **2**" in rendered
    assert "Flask live log: available" in rendered
    assert "# Alice Pro validation report" in capsys.readouterr().out


def test_validation_report_main_handles_missing_coverage_and_frontend_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    report.main()

    rendered = (tmp_path / "ci-validation-report.md").read_text(encoding="utf-8")
    assert "V8 JavaScript coverage: **not available**" in rendered
    assert "Coverage report was not produced." in rendered
    assert "Flask live log: not available" in rendered
    assert "**not run**" in rendered
