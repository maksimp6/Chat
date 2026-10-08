"""Offline safety tests for Android RDC watchdog."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "android" / "scripts"


class WatchdogTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("termux-rdc-watchdog.sh", "install-rdc-watchdog.sh", "termux-rdc.sh"):
            subprocess.run(["bash", "-n", str(ROOT / name)], check=True)

    def test_no_self_restart_and_backoff(self):
        text = (ROOT / "termux-rdc-watchdog.sh").read_text()
        self.assertIn("flock -n 9", text)
        self.assertIn("/proc/$pid/cmdline", text)
        self.assertIn("now - last_attempt >= BACKOFF", text)
        self.assertNotIn('"$RDC_SCRIPT" restart', text)
        self.assertNotIn('"$RDC_SCRIPT" stop', text)

    def test_existing_boot_hook_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            boot = home / ".termux" / "boot"
            boot.mkdir(parents=True)
            original = boot / "20-alice-rdc"
            original.write_text("keep")
            env = dict(os.environ, HOME=tmp)
            result = subprocess.run(
                ["bash", str(ROOT / "install-rdc-watchdog.sh")],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 3)
            self.assertEqual(original.read_text(), "keep")
            self.assertFalse((boot / "20-alice-rdc-watchdog").exists())

    def test_installer_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp)
            cmd = ["bash", str(ROOT / "install-rdc-watchdog.sh")]
            first = subprocess.run(cmd, env=env, capture_output=True)
            self.assertEqual(first.returncode, 0)
            hook = Path(tmp) / ".termux" / "boot" / "20-alice-rdc-watchdog"
            contents = hook.read_bytes()
            second = subprocess.run(cmd, env=env, capture_output=True)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(hook.read_bytes(), contents)


    def test_missing_agent_triggers_start_without_touching_real_rdc(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fake_bin = home / "bin"
            fake_bin.mkdir()
            launcher = home / "fake-launcher.sh"
            marker = home / "started"
            launcher.write_text('#!/bin/bash\necho "$1" >> "$MARKER"\n')
            sleeper = fake_bin / "sleep"
            sleeper.write_text('#!/bin/sh\nexit 17\n')
            sleeper.chmod(0o755)
            env = dict(
                os.environ,
                HOME=tmp,
                RDC_HOME=str(home / "rdc"),
                RDC_SCRIPT=str(launcher),
                RDC_WATCHDOG_INTERVAL="10",
                RDC_WATCHDOG_BACKOFF="30",
                MARKER=str(marker),
                PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
            )
            result = subprocess.run(
                ["bash", str(ROOT / "termux-rdc-watchdog.sh")],
                env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 17)
            self.assertEqual(marker.read_text().splitlines(), ["start"])

if __name__ == "__main__":
    unittest.main()
