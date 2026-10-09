"""Offline tests for the restricted Android actions CLI."""
import argparse
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest

MODULE = Path(__file__).resolve().parents[2] / "tools/android/rdc_actions.py"
spec = importlib.util.spec_from_file_location("rdc_actions", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def args(command, **kw):
    values = dict(command=command, x=0, y=0, x2=0, y2=0, ms=300, package="", output_dir="/tmp")
    values.update(kw)
    return argparse.Namespace(**values)

def test_no_devices():
    with patch.object(module, "run", return_value="List of devices attached\n"):
        with pytest.raises(RuntimeError):
            module.device()

def test_multiple_devices_rejected():
    with patch.object(module, "run", return_value="List of devices attached\na\tdevice\nb\tdevice\n"):
        with pytest.raises(RuntimeError):
            module.device()

def test_tap_submitted_not_verified():
    with patch.object(module, "device", return_value="serial"), patch.object(module, "run") as runner:
        assert module.execute(args("tap", x=200, y=300)) == {"command": "tap", "submitted": True, "verified": False}
        runner.assert_called_once_with("-s", "serial", "shell", "input", "tap", 200, 300)

def test_reject_invalid_coordinates():
    with patch.object(module, "device", return_value="serial"):
        with pytest.raises(ValueError):
            module.execute(args("tap", x=-1))

def test_reject_invalid_package():
    with patch.object(module, "device", return_value="serial"):
        with pytest.raises(ValueError):
            module.execute(args("open", package="com.example;rm"))

def test_home_uses_keyevent():
    with patch.object(module, "device", return_value="serial"), patch.object(module, "run") as runner:
        module.execute(args("home"))
        runner.assert_called_once_with("-s", "serial", "shell", "input", "keyevent", "KEYCODE_HOME")

def test_status_parses_focus():
    with patch.object(module, "device", return_value="serial"), patch.object(module, "run", return_value="mCurrentFocus=Window{123 app}\n"):
        assert module.execute(args("status"))["focus"] == "Window{123 app}"
