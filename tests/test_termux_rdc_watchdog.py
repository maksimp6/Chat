
"""Offline checks for independent Android RDC watchdog."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / "android" / "scripts"


class WatchdogTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("termux-rdc-watchdog.sh", "install-rdc-watchdog.sh"):
            subprocess.run(["bash", "-n", str(ROOT / name)], check=True)

    def test_independent_and_idempotent(self):
        text = (ROOT / "termux-rdc-watchdog.sh").read_text()
        self.assertIn("flock -n 9", text)
        self.assertIn("/proc/$pid/cmdline", text)
        self.assertNotIn('"$RDC_SCRIPT" restart', text)
        self.assertNotIn('"$RDC_SCRIPT" stop', text)

    def test_boot_hook(self):
        text = (ROOT / "install-rdc-watchdog.sh").read_text()
        self.assertIn("20-alice-rdc-watchdog", text)
        self.assertNotIn("nohup", text)


if __name__ == "__main__":
    unittest.main()
