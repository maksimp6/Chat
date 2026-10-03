import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "measure_critical_bundle.py"

_spec = importlib.util.spec_from_file_location("measure_critical_bundle", SCRIPT_PATH)
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_critical_set_matches_the_data_critical_script_contract():
    names = [label for label, _ in bundle.discover_critical_files()]
    assert names == [
        "templates/index.html",
        "static/style.css",
        "static/boot.js",
        "static/core_api.js",
        "static/ui_runtime.js",
        "static/dispatcher.js",
        "static/core.js",
    ]


def test_full_page_set_is_a_superset_of_the_critical_set():
    critical = {label for label, _ in bundle.discover_critical_files()}
    full = {label for label, _ in bundle.discover_full_page_files()}
    assert critical <= full
    assert "static/chat.js" in full
    assert "static/sidebar.js" in full


def test_critical_set_stays_within_the_documented_ci_budget():
    rows = bundle.measure(bundle.discover_critical_files())
    total_raw = sum(row["raw_bytes"] for row in rows)
    assert total_raw <= bundle.DEFAULT_LIMIT_BYTES


def test_cli_check_passes_against_the_documented_budget():
    result = _run("--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: critical set is within the CI budget" in result.stdout


def test_cli_check_fails_against_a_deliberately_tight_budget():
    result = _run("--check", "--limit-bytes", "1024")
    assert result.returncode == 1
    assert "FAIL: critical set is" in result.stdout
