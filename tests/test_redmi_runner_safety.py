import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load("redmi_python_runner_under_test", "scripts/redmi_python_runner.py")
ram = _load("ram_test_runner_under_test", "scripts/ram_test_runner.py")


def test_workflow_input_is_resolved_from_the_job_context():
    assert runner.decode_env({"expr": "inputs.mode"}, {}, {}, {"mode": "read"}) == "read"


def test_other_expressions_are_still_refused():
    with pytest.raises(ValueError):
        runner.decode_env({"expr": "env.anything"}, {}, {}, {"mode": "read"})
    with pytest.raises(ValueError):
        runner.decode_env({"expr": "inputs.missing"}, {}, {}, {"mode": "read"})


def test_ram_fingerprint_notices_a_changed_kicad_schematic(monkeypatch, tmp_path):
    monkeypatch.setattr(ram, "ROOT", tmp_path)
    schematic = tmp_path / "board.kicad_sch"
    schematic.write_text("version 1")
    before = ram.fingerprint(["board.kicad_sch"])

    schematic.write_text("version 2")

    assert ram.fingerprint(["board.kicad_sch"]) != before
