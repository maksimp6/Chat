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


if __name__ == "__main__":
    unittest.main()
