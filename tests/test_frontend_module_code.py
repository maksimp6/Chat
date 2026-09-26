from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_modules_code_contract():
    """Run the authoritative static module validator as one deterministic test."""
    validator = ROOT / "tests" / "validate_frontend_modules.py"
    result = subprocess.run(
        [sys.executable, str(validator)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "frontend module code validation failed:\n" + (result.stdout or "") + (result.stderr or "")
    )
