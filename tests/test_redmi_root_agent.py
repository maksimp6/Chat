"""Offline RootAgent controls; execute only in the authorized Actions runner.

Android commands, root escalation and the production socket are never used.
"""
import base64
import binascii
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


RUNNER_KEYS = ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_SHA", "RUNNER_NAME", "RUNNER_TEMP")
if (os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true"
        or not all(os.environ.get(key) for key in RUNNER_KEYS)):
    raise RuntimeError("RootAgent tests require an actual GitHub Actions runner")

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "redmi_root_agent.py"
SPEC = importlib.util.spec_from_file_location("redmi_root_agent_under_test", SOURCE)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff))


def png(width=2, height=2, color=2, compressed=None, pixels=None):
    if compressed is None:
        channels = 4 if color == 6 else 3
        if pixels is None:
            pixels = (b"\0" + bytes(range(channels)) * width) * height
        compressed = zlib.compress(pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def connection_fixture():
    connection = mock.MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = b"OK\n"
    return connection


def mouse_request(command="click", dx=8, dy=-2):
    return {"command": command, "dx": dx, "dy": dy,
            "expected_package": "com.example.fixture"}


def screen_fixture(package="com.example.fixture", window_id="abc", unlocked=True, display_on=True):
    return {"unlocked": unlocked, "display_on": display_on, "package": package,
            "focus_window": f"Window{{{window_id} u0 {package}/MainActivity}}"}


class PngValidation(unittest.TestCase):
    def test_rgb_and_rgba_are_fully_decoded_and_hashed(self):
        for color in (2, 6):
            with self.subTest(color=color):
                image = png(color=color)
                metadata = agent.validate_png(image)
                self.assertEqual((metadata["width"], metadata["height"]), (2, 2))
                self.assertEqual(metadata["bytes"], len(image))
                self.assertEqual(metadata["sha256"], hashlib.sha256(image).hexdigest())

    def test_crc_damage_is_rejected(self):
        damaged = bytearray(png())
        damaged[-1] ^= 1
        with self.assertRaisesRegex(agent.AgentError, "crc"):
            agent.validate_png(bytes(damaged))

    def test_invalid_zlib_with_valid_chunk_crc_is_rejected(self):
        with self.assertRaises((agent.AgentError, zlib.error)):
            agent.validate_png(png(compressed=b"not a zlib stream"))

    def test_incomplete_zlib_with_valid_chunk_crc_is_rejected(self):
        stream = zlib.compress((b"\0" + b"\1" * 6) * 2)
        with self.assertRaises(agent.AgentError):
            agent.validate_png(png(compressed=stream[:-2]))

    def test_truncation_and_trailing_png_bytes_are_rejected(self):
        for image in (png()[:-5], png() + b"unexpected trailing bytes"):
            with self.subTest(length=len(image)), self.assertRaises(agent.AgentError):
                agent.validate_png(image)

    def test_invalid_dimensions_fail_before_pixel_allocation(self):
        for width, height in ((0, 1), (8193, 1), (8192, 2049)):
            with self.subTest(width=width, height=height):
                with self.assertRaisesRegex(agent.AgentError, "dimensions"):
                    agent.validate_png(png(width, height, compressed=zlib.compress(b"\0")))

    def test_wrong_pixel_length_filter_and_second_zlib_stream_are_rejected(self):
        good_pixels = (b"\0" + b"\1" * 6) * 2
        invalid = (png(pixels=b"\0"), png(pixels=good_pixels + b"extra"),
                   png(pixels=b"\5" + good_pixels[1:]),
                   png(compressed=zlib.compress(good_pixels) + zlib.compress(b"extra")))
        for image in invalid:
            with self.subTest(image_hash=hashlib.sha256(image).hexdigest()):
                with self.assertRaises(agent.AgentError):
                    agent.validate_png(image)


class ScreenAndInputControls(unittest.TestCase):
    def test_only_explicit_unlocked_policy_is_accepted(self):
        policy = b"showing=false\nmInputRestricted=false\nmKeyguardOccluded=false\n"
        window = (b"mCurrentFocus=Window{abc u0 com.example.fixture/MainActivity}\n"
                  b"mScreenOnEarly=true mScreenOnFully=true\n")
        cases = ((policy, True),
                 (policy.replace(b"mKeyguardOccluded=false\n", b"mKeyguardOccluded=false mKeyguardOccludedChanged=false\n"), True),
                 (policy.replace(b"showing=false", b"showing=true"), False),
                 (policy.replace(b"mInputRestricted=false\n", b""), False),
                 (policy + b"showing=true\n", False),
                 (b"unrecognized output\n", False))
        for raw, unlocked in cases:
            with self.subTest(policy=raw):
                with mock.patch.object(agent, "bounded_command", side_effect=[raw, window]):
                    state = agent.screen_state()
                self.assertIs(state["unlocked"], unlocked)
                self.assertEqual(state["package"], "com.example.fixture")
                self.assertEqual(state["focus_window"], "Window{abc u0 com.example.fixture/MainActivity}")
                self.assertTrue(state["display_on"])

    def test_display_requires_both_explicit_true_flags(self):
        policy = b"showing=false mInputRestricted=false mKeyguardOccluded=false\n"
        window = (b"mCurrentFocus=Window{abc u0 com.example.fixture/MainActivity}\n"
                  b"mScreenOnEarly=true mScreenOnFully=true\n")
        cases = ((window, True),
                 (window.replace(b"mScreenOnEarly=true", b"mScreenOnEarly=false"), False),
                 (window.replace(b"mScreenOnFully=true", b"mScreenOnFully=false"), False),
                 (window.replace(b"mScreenOnFully=true", b""), False),
                 (window + b"mScreenOnFully=false\n", False))
        for raw, display_on in cases:
            with self.subTest(window=raw):
                with mock.patch.object(agent, "bounded_command", side_effect=[policy, raw]):
                    state = agent.screen_state()
                self.assertIs(state["display_on"], display_on)
                self.assertTrue(state["unlocked"])

    def test_policy_only_display_flags_cannot_authorize_capture(self):
        policy = (b"showing=false mInputRestricted=false mKeyguardOccluded=false\n"
                  b"mScreenOnEarly=true mScreenOnFully=true\n")
        window = b"mCurrentFocus=Window{abc u0 com.example.fixture/MainActivity}\n"
        with mock.patch.object(agent, "bounded_command", side_effect=[policy, window]):
            state = agent.screen_state()
        self.assertIs(state["display_on"], False)

    def test_secondary_off_display_does_not_override_default_display(self):
        policy = b"showing=false mInputRestricted=false mKeyguardOccluded=false\n"
        window = (b"mCurrentFocus=Window{abc u0 com.example.fixture/MainActivity}\n"
                  b"Display: mDisplayId=0\nmScreenOnEarly=true mScreenOnFully=true\n"
                  b"Display: mDisplayId=1\nmScreenOnEarly=false mScreenOnFully=false\n")
        with mock.patch.object(agent, "bounded_command", side_effect=[policy, window]):
            self.assertIs(agent.screen_state()["display_on"], True)
        reversed_states = window.replace(b"=true", b"=temporary").replace(b"=false", b"=true").replace(b"=temporary", b"=false")
        with mock.patch.object(agent, "bounded_command", side_effect=[policy, reversed_states]):
            self.assertIs(agent.screen_state()["display_on"], False)

    def test_status_never_connects_to_mouse(self):
        state = screen_fixture("com.example.private", unlocked=False)
        with (mock.patch.object(agent, "device_guard"),
              mock.patch.object(agent, "screen_state", return_value=state),
              mock.patch.object(agent, "mouse_metadata"),
              mock.patch.object(agent, "mouse_connection") as connect):
            result = agent.worker({"command": "status"})
        connect.assert_not_called()
        self.assertTrue(result["keyguard_locked_or_unknown"])
        self.assertTrue(result["focus_present"])
        self.assertTrue(result["display_on"])
        self.assertNotIn("package", result)
        self.assertNotIn("com.example.private", str(result))

    def test_locked_or_unknown_capture_never_invokes_screencap(self):
        states = (screen_fixture(unlocked=False),
                  {"unlocked": False, "display_on": True, "package": None, "focus_window": None},
                  {"unlocked": True, "display_on": True, "package": None, "focus_window": None},
                  {"unlocked": True, "display_on": True, "package": "com.example.fixture", "focus_window": None})
        for state in states:
            with self.subTest(state=state):
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "screen_state", return_value=state),
                      mock.patch.object(agent, "bounded_command") as command):
                    with self.assertRaisesRegex(agent.AgentError, "screen_locked_or_unknown"):
                        agent.worker({"command": "capture"})
                command.assert_not_called()

    def test_sleeping_or_unknown_display_never_captures_or_sends_input(self):
        for display_on in (False, None):
            for request in ({"command": "capture"}, mouse_request("move")):
                with self.subTest(display_on=display_on, command=request["command"]):
                    connection = connection_fixture()
                    with (mock.patch.object(agent, "device_guard"),
                          mock.patch.object(agent, "screen_state", return_value=screen_fixture(display_on=display_on)),
                          mock.patch.object(agent, "bounded_command") as command,
                          mock.patch.object(agent, "mouse_connection", return_value=connection)):
                        with self.assertRaisesRegex(agent.AgentError, "screen_not_awake"):
                            agent.worker(request)
                    command.assert_not_called()
                    connection.sendall.assert_not_called()

    def test_status_reports_sleep_without_exposing_focus_content(self):
        with (mock.patch.object(agent, "device_guard"),
              mock.patch.object(agent, "screen_state", return_value=screen_fixture("com.example.private", display_on=False)),
              mock.patch.object(agent, "mouse_metadata")):
            result = agent.worker({"command": "status"})
        self.assertIs(result["display_on"], False)
        self.assertIs(result["keyguard_locked_or_unknown"], False)
        self.assertNotIn("package", result)
        self.assertNotIn("com.example.private", str(result))

    def test_capture_discards_image_if_focus_changes_or_screen_locks(self):
        before = screen_fixture()
        for after in (screen_fixture("com.other.app"), screen_fixture(unlocked=False), screen_fixture(display_on=False),
                      screen_fixture(window_id="different")):
            with self.subTest(after=after):
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "screen_state", side_effect=[before, after]),
                      mock.patch.object(agent, "bounded_command", return_value=png()) as command):
                    with self.assertRaises(agent.AgentError):
                        agent.worker({"command": "capture"})
                self.assertEqual(command.call_count, 1)
                self.assertEqual(command.call_args.args[0], ["/system/bin/screencap", "-p"])

    def test_capture_accepts_optional_expected_package_without_changing_default_api(self):
        for request in ({"command": "capture"},
                        {"command": "capture", "expected_package": "com.example.fixture"}):
            with self.subTest(request=request):
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "screen_state", return_value=screen_fixture()),
                      mock.patch.object(agent, "bounded_command", return_value=png())):
                    result = agent.worker(request)
                self.assertEqual(base64.b64decode(result["png_base64"]), png())

    def test_capture_rejects_wrong_or_invalid_expected_package_before_screencap(self):
        for expected in ("com.other.app", "bad;command", "", None, 123):
            with self.subTest(expected=expected):
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "screen_state", return_value=screen_fixture()),
                      mock.patch.object(agent, "bounded_command") as command):
                    with self.assertRaises(agent.AgentError):
                        agent.worker({"command": "capture", "expected_package": expected})
                command.assert_not_called()

    def test_wrong_foreground_never_sends_socket_bytes(self):
        connection = connection_fixture()
        with (mock.patch.object(agent, "device_guard"),
              mock.patch.object(agent, "screen_state", return_value=screen_fixture("com.other.app")),
              mock.patch.object(agent, "mouse_connection", return_value=connection)):
            with self.assertRaisesRegex(agent.AgentError, "foreground_package_mismatch"):
                agent.worker(mouse_request("move"))
        connection.sendall.assert_not_called()

    def test_unknown_fields_unknown_commands_and_invalid_deltas_never_connect(self):
        requests = ({"command": "tap"}, {"command": "status", "shell": "forbidden"},
                    mouse_request(dx=501), mouse_request(dy=-501), mouse_request(dx=True),
                    {"command": "move", "dx": 0, "dy": 0},
                    {**mouse_request(), "expected_package": "bad;command"})
        for request in requests:
            with self.subTest(request=request):
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "mouse_connection") as connect):
                    with self.assertRaises(agent.AgentError):
                        agent.worker(request)
                connect.assert_not_called()

    def test_fragmented_ack_is_submission_only_and_deltas_remain_relative(self):
        for command, dx, dy in (("click", 8, -2), ("move", -500, 500)):
            with self.subTest(command=command):
                connection = connection_fixture()
                connection.recv.side_effect = [b"O", b"K\n"]
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "require_screen"),
                      mock.patch.object(agent, "mouse_connection", return_value=connection)):
                    result = agent.worker(mouse_request(command, dx, dy))
                connection.sendall.assert_called_once_with(f"{command} {dx} {dy}\n".encode())
                self.assertIs(result["submitted"], True)
                self.assertIs(result["verified"], False)
                self.assertEqual(result["coordinates"], "relative")

    def test_error_ack_and_timeout_never_return_success_or_retry(self):
        for reply in (b"ERR\n", b"", socket.timeout("synthetic timeout")):
            with self.subTest(reply_type=type(reply).__name__):
                connection = connection_fixture()
                if isinstance(reply, Exception):
                    connection.recv.side_effect = reply
                else:
                    connection.recv.return_value = reply
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "require_screen"),
                      mock.patch.object(agent, "mouse_connection", return_value=connection)):
                    with self.assertRaisesRegex(agent.AgentError, "do_not_retry"):
                        agent.worker(mouse_request())
                connection.sendall.assert_called_once()


class WakeControls(unittest.TestCase):
    def test_wake_sends_only_fixed_hover_and_ack_never_proves_wakeup(self):
        for display_on in (False, None):
            with self.subTest(display_on=display_on):
                connection = connection_fixture()
                state = {"unlocked": False, "display_on": display_on,
                         "package": None, "focus_window": None}
                with (mock.patch.object(agent, "device_guard") as guard,
                      mock.patch.object(agent, "screen_state", return_value=state),
                      mock.patch.object(agent, "require_screen") as screen_guard,
                      mock.patch.object(agent, "mouse_connection", return_value=connection) as connect):
                    result = agent.worker({"command": "wake"})
                guard.assert_called_once()
                connect.assert_called_once()
                screen_guard.assert_not_called()
                connection.sendall.assert_called_once_with(b"move 1 1\n")
                self.assertEqual(result["command"], "wake")
                self.assertIs(result["submitted"], True)
                self.assertIs(result["verified"], False)
                self.assertEqual(result["coordinates"], "relative")

    def test_already_awake_never_connects_or_sends(self):
        with (mock.patch.object(agent, "device_guard"),
              mock.patch.object(agent, "screen_state", return_value=screen_fixture()),
              mock.patch.object(agent, "mouse_connection") as connect):
            result = agent.worker({"command": "wake"})
        connect.assert_not_called()
        self.assertTrue(result["already_awake"])
        self.assertEqual(result["input_operations"], "NOT_RUN")
        self.assertIs(result["verified"], False)
        self.assertNotIn("submitted", result)

    def test_wake_rechecks_awake_state_before_sending(self):
        connection = connection_fixture()
        with (mock.patch.object(agent, "device_guard"),
              mock.patch.object(agent, "screen_state", side_effect=[
                  screen_fixture(display_on=False), screen_fixture()]),
              mock.patch.object(agent, "mouse_connection", return_value=connection)):
            result = agent.worker({"command": "wake"})
        connection.sendall.assert_not_called()
        self.assertTrue(result["already_awake"])

    def test_wake_rejects_any_caller_controlled_payload(self):
        for extra in ({"dx": 100}, {"dx": 1, "dy": 1},
                      {"expected_package": "com.example.fixture"}, {"command_args": "click 1 1"}):
            with self.subTest(extra=extra):
                with (mock.patch.object(agent, "device_guard") as guard,
                      mock.patch.object(agent, "mouse_connection") as connect):
                    with self.assertRaisesRegex(agent.AgentError, "invalid_worker_request"):
                        agent.worker({"command": "wake", **extra})
                guard.assert_not_called()
                connect.assert_not_called()

    def test_wake_unknown_ack_or_timeout_never_retries_or_reports_success(self):
        for reply in (b"ERR\n", socket.timeout("synthetic timeout")):
            with self.subTest(reply_type=type(reply).__name__):
                connection = connection_fixture()
                if isinstance(reply, Exception):
                    connection.recv.side_effect = reply
                else:
                    connection.recv.return_value = reply
                with (mock.patch.object(agent, "device_guard"),
                      mock.patch.object(agent, "screen_state", return_value=screen_fixture(display_on=False)),
                      mock.patch.object(agent, "mouse_connection", return_value=connection)):
                    with self.assertRaisesRegex(agent.AgentError, "do_not_retry"):
                        agent.worker({"command": "wake"})
                connection.sendall.assert_called_once_with(b"move 1 1\n")


class RunnerBoundaryControls(unittest.TestCase):
    def test_public_and_worker_entry_deny_missing_runner_before_any_work(self):
        for argument in ("status", "_worker"):
            with self.subTest(argument=argument):
                with (mock.patch.object(agent.os, "environ", {}),
                      mock.patch.object(agent.sys, "argv", [str(SOURCE), argument]),
                      mock.patch.object(agent.sys, "stdin") as stdin,
                      mock.patch.object(agent.subprocess, "run") as launch,
                      mock.patch.object(agent, "worker") as worker):
                    with self.assertRaisesRegex(agent.AgentError, "actual_github_runner_required"):
                        agent.main()
                launch.assert_not_called()
                worker.assert_not_called()
                stdin.buffer.read.assert_not_called()

    def test_su_preserves_real_runner_values_and_excludes_loader_hooks(self):
        genuine = agent.runner_guard()
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "PREFIX", "LANG")
                       if key in os.environ}
        environment.update(genuine)
        environment.update({"LD_PRELOAD": "synthetic-hook", "LD_LIBRARY_PATH": "synthetic-libraries"})
        reply = SimpleNamespace(returncode=0, stdout=b'{"ok":true,"result":{"root_uid":0}}')
        with (mock.patch.object(agent.os, "environ", environment),
              mock.patch.object(agent.sys, "argv", [str(SOURCE), "status"]),
              mock.patch.object(agent.sys, "stdout", io.StringIO()),
              mock.patch.object(agent.subprocess, "run", return_value=reply) as launch):
            self.assertEqual(agent.main(), 0)
        argv = launch.call_args.args[0]
        self.assertEqual(argv[:2], ["su", "-c"])
        words = shlex.split(argv[2])
        self.assertEqual(dict(word.split("=", 1) for word in words[:len(RUNNER_KEYS)]), genuine)
        self.assertTrue(Path(words[len(RUNNER_KEYS)]).is_absolute())
        self.assertEqual(words[-1], "_worker")
        passed = launch.call_args.kwargs["env"]
        self.assertNotIn("LD_PRELOAD", passed)
        self.assertNotIn("LD_LIBRARY_PATH", passed)
        self.assertEqual({key: passed[key] for key in RUNNER_KEYS}, genuine)

    def test_capture_cli_serializes_optional_package_and_never_prints_image_payload(self):
        encoded = base64.b64encode(png()).decode("ascii")
        for extra in ([], ["--expected-package", "com.example.fixture"]):
            with self.subTest(extra=extra):
                reply = SimpleNamespace(returncode=0, stdout=json.dumps({
                    "ok": True, "result": {"png_base64": encoded}}).encode())
                printed = io.StringIO()
                with (mock.patch.object(agent.sys, "argv", [str(SOURCE), "capture", "--output", "new.png", *extra]),
                      mock.patch.object(agent.os, "geteuid", return_value=10288),
                      mock.patch.object(agent.sys, "stdout", printed),
                      mock.patch.object(agent.subprocess, "run", return_value=reply) as launch,
                      mock.patch.object(agent, "save_capture") as save):
                    self.assertEqual(agent.main(), 0)
                request = json.loads(launch.call_args.kwargs["input"])
                self.assertEqual(request["command"], "capture")
                if extra:
                    self.assertEqual(request["expected_package"], "com.example.fixture")
                else:
                    self.assertNotIn("expected_package", request)
                save.assert_called_once_with("new.png", png())
                self.assertNotIn(encoded, printed.getvalue())
                self.assertNotIn("png_base64", printed.getvalue())


class SocketIdentityControls(unittest.TestCase):
    def test_nonroot_peer_is_rejected_before_executable_read_or_send(self):
        connection = connection_fixture()
        connection.getsockopt.return_value = struct.pack("3i", 123, 10288, 10288)
        with (mock.patch.object(agent, "mouse_metadata"),
              mock.patch.object(agent.socket, "socket", return_value=connection),
              mock.patch.object(agent, "open", create=True) as executable):
            with self.assertRaisesRegex(agent.AgentError, "mouse_peer_not_root"):
                agent.mouse_connection()
        executable.assert_not_called()
        connection.sendall.assert_not_called()
        connection.close.assert_called_once()

    def test_unexpected_root_binary_is_rejected_without_send(self):
        connection = connection_fixture()
        connection.getsockopt.return_value = struct.pack("3i", 123, 0, 0)
        executable = mock.MagicMock()
        executable.__enter__.return_value = executable
        executable.read.side_effect = [b"different executable", b""]
        with (mock.patch.object(agent, "mouse_metadata"),
              mock.patch.object(agent.socket, "socket", return_value=connection),
              mock.patch.object(agent, "open", return_value=executable, create=True),
              mock.patch.object(agent.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o755))):
            with self.assertRaisesRegex(agent.AgentError, "mouse_executable_mismatch"):
                agent.mouse_connection()
        connection.sendall.assert_not_called()
        connection.close.assert_called_once()


class CaptureFileControls(unittest.TestCase):
    def test_capture_is_exact_bytes_and_mode_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            image = png(color=6)
            agent.save_capture(path, image)
            self.assertEqual(path.read_bytes(), image)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.png"
            path.write_bytes(b"original bytes")
            with self.assertRaises(FileExistsError):
                agent.save_capture(path, png())
            self.assertEqual(path.read_bytes(), b"original bytes")

    def test_symlink_and_its_target_are_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            target, link = Path(directory) / "target", Path(directory) / "capture.png"
            target.write_bytes(b"original target")
            link.symlink_to(target)
            with self.assertRaises(OSError):
                agent.save_capture(link, png())
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), b"original target")


if __name__ == "__main__":
    unittest.main()
