"""Offline tests for the restricted Android actions CLI."""
import argparse
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys
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


def test_unknown_action_is_rejected_before_adb():
    with patch.object(module, "device") as connected:
        with pytest.raises(ValueError, match="Unknown Android action"):
            module.execute(args("unexpected"))
        connected.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("synthetic-private-path"),
        PermissionError("synthetic-private-path"),
        subprocess.TimeoutExpired("synthetic-private-command", 20),
        subprocess.CalledProcessError(1, "synthetic-private-command"),
    ],
)
def test_cli_errors_are_generic_json(failure, monkeypatch, capsys):
    monkeypatch.setattr(module.sys, "argv", ["rdc_actions", "status"])
    with patch.object(module, "execute", side_effect=failure):
        assert module.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {
        "error": type(failure).__name__,
        "message": "Android action failed",
    }
    assert "synthetic-private" not in output.err


def test_cli_reports_submitted_without_claiming_verified(monkeypatch, capsys):
    monkeypatch.setattr(module.sys, "argv", ["rdc_actions", "home"])
    with patch.object(module, "device", return_value="synthetic-device"), patch.object(module, "run"):
        assert module.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"command": "home", "submitted": True, "verified": False}
    assert output.err == ""


@pytest.mark.parametrize("binary", [False, True])
def test_adb_process_is_bounded_and_preserves_binary(binary):
    with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"data\n")) as process:
        assert module.run("devices", binary=binary) == (b"data\n" if binary else "data")
    assert process.call_args.args[0] == ["adb", "devices"]
    assert process.call_args.kwargs["timeout"] == 20
    assert process.call_args.kwargs["check"] is True
    assert process.call_args.kwargs["capture_output"] is True


def test_connected_device_ignores_offline_and_unauthorized_rows():
    with patch.object(module, "run", return_value="List of devices attached\noffline offline\nlocked unauthorized\nsynthetic device\n"):
        assert module.device() == "synthetic"


def test_status_without_focus_does_not_invent_an_app():
    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run", return_value="No active window"):
        assert module.execute(args("status")) == {"device_connected": True, "focus": None}


def test_valid_swipe_is_submitted_with_bounded_duration():
    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run") as process:
        result = module.execute(args("swipe", x=1, y=2, x2=3, y2=4, ms=500))
        assert result == {"command": "swipe", "submitted": True, "verified": False}
        process.assert_called_once_with("-s", "synthetic", "shell", "input", "swipe", 1, 2, 3, 4, 500)


@pytest.mark.parametrize("duration", [49, 10001])
def test_swipe_outside_duration_limit_does_not_submit(duration):
    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run") as process:
        with pytest.raises(ValueError, match="duration"):
            module.execute(args("swipe", ms=duration))
        process.assert_not_called()


def test_back_and_open_preserve_restricted_commands():
    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run") as process:
        module.execute(args("back"))
        process.assert_called_once_with("-s", "synthetic", "shell", "input", "keyevent", "KEYCODE_BACK")
        process.reset_mock()
        module.execute(args("open", package="com.example.app"))
        process.assert_called_once_with("-s", "synthetic", "shell", "monkey", "-p", "com.example.app", "1")


@pytest.mark.parametrize("data", [b"invalid", "invalid"])
def test_invalid_screenshot_never_runs_converter(data):
    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run", return_value=data), patch.object(module.subprocess, "run") as converter:
        with pytest.raises(RuntimeError, match="screenshot"):
            module.execute(args("screenshot"))
        converter.assert_not_called()


def test_screenshot_result_is_private_and_comes_from_converter(tmp_path):
    directory = tmp_path / "private"
    png = module.PNG_MAGIC + b"synthetic-image"
    jpeg = b"synthetic-jpeg"

    def convert(command, **options):
        assert options["input"] == png
        assert options["timeout"] == 20
        assert options["check"] is True
        assert command[0] == "ffmpeg"
        Path(command[-1]).write_bytes(jpeg)
        return subprocess.CompletedProcess(command, 0)

    with patch.object(module, "device", return_value="synthetic"), patch.object(module, "run", return_value=png), patch.object(module.subprocess, "run", side_effect=convert):
        result = module.execute(args("screenshot", output_dir=str(directory)))
    assert result == {"path": str(directory / "latest.jpg"), "bytes": len(jpeg)}
    assert (directory / "latest.jpg").read_bytes() == jpeg
    assert (directory / "latest.jpg").stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700


def test_script_entrypoint_status_uses_only_synthetic_adb(tmp_path, monkeypatch, capsys):
    adb = tmp_path / "adb"
    adb.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['devices']:\n"
        "    print('List of devices attached\\nsynthetic device')\n"
        "else:\n"
        "    assert sys.argv[1:] == ['-s', 'synthetic', 'shell', 'dumpsys', 'window']\n"
        "    print('mCurrentFocus=Window{fixture}')\n",
        encoding="utf-8",
    )
    adb.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [str(MODULE), "status"])
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(str(MODULE), run_name="__main__")
    assert exit_status.value.code == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"device_connected": True, "focus": "Window{fixture}"}
    assert output.err == ""
