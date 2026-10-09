"""Process-crash recovery using real subprocesses and real filesystem."""

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alice_mouse_process_switch import alive, digest

HOME = Path(__file__).resolve().parent
SH = "/system/bin/sh" if Path("/system/bin/sh").exists() else "/bin/sh"
BOOT = str(HOME / "alice_mouse_watchdog_v2.sh")


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="alice-crash-")
        self.addCleanup(self.t.cleanup)
        self.dir = Path(self.t.name)
        (self.dir / "staging").mkdir()
        self.backup = self.dir / "staging/rollback-guard"
        self.target = self.dir / "mouse"
        self.candidate = self.dir / "candidate"
        self._program(self.backup)
        self._program(self.candidate)
        self.target.write_bytes(self.backup.read_bytes())
        self.target.chmod(0o700)
        self.old = subprocess.Popen(
            [str(self.target), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.new_pids = []
        self.addCleanup(self._cleanup)

    def _program(self, file):
        file.write_text(f"#!{sys.executable}\nimport time\nwhile True: time.sleep(0.05)\n")
        file.chmod(0o700)

    def _cleanup(self):
        for pid in [self.old.pid, *self.new_pids]:
            if pid and alive(pid):
                os.kill(pid, signal.SIGTERM)
        self.old.wait(timeout=2)

    def _run_crash(self, point):
        config = {
            "target": str(self.target),
            "candidate": str(self.candidate),
            "backup": str(self.backup),
            "old_pid": self.old.pid,
            "lock": str(self.dir / "tx.lock"),
            "journal": str(self.dir / "journal"),
            "pending": str(self.dir / "cutover.pending"),
        }
        code = (
            "import os\n"
            "from pathlib import Path\n"
            "from alice_mouse_process_switch import ProcessSwitch,SwitchConfig\n"
            f"c={config!r}\n"
            "cfg=SwitchConfig(target=Path(c['target']),candidate=Path(c['candidate']),"
            "backup=Path(c['backup']),old_pid=c['old_pid'],lock=Path(c['lock']),"
            "journal=Path(c['journal']),pending=Path(c['pending']),timeout=1.2)\n"
            "class Crash(ProcessSwitch):\n"
            " def _stop(self,pid):\n"
            + ("  os._exit(77)\n" if point == "before_stop" else "  return super()._stop(pid)\n")
            + " def _spawn(self):\n"
            + ("  os._exit(78)\n" if point == "before_spawn" else "  return super()._spawn()\n")
            + "Crash(cfg,external_mouse_active=lambda: False).run()\n"
        )
        p = subprocess.run(
            [sys.executable, "-c", code],
            cwd=HOME,
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return p.returncode

    def boot_once(self):
        env = os.environ.copy()
        env["ALICE_MOUSE_TEST_MODE"] = "1"
        env["ALICE_MOUSE_ROOT"] = str(self.dir)
        return subprocess.run(
            [SH, BOOT], env=env, check=False, capture_output=True, text=True, timeout=3
        )

    def recover_simulated_next_boot(self):
        pending = self.dir / "cutover.pending"
        self.assertTrue(pending.exists())
        self.assertNotEqual(
            self.boot_once().returncode, 0, "same-boot watchdog must leave fresh transaction alone"
        )
        pending.write_text("another-boot-id\n")
        (self.dir / "synthetic_active").touch()
        self.assertNotEqual(
            self.boot_once().returncode, 0, "never restore while old mouse is active"
        )
        self.assertTrue(pending.exists())
        if alive(self.old.pid):
            os.kill(self.old.pid, signal.SIGTERM)
            self.old.wait(timeout=2)
        (self.dir / "synthetic_active").unlink()
        restored = self.boot_once()
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertFalse(pending.exists())
        self.assertEqual(digest(self.target), digest(self.backup))

    def test_supervisor_crashes_after_install(self):
        self.candidate.write_bytes(
            b"#!" + sys.executable.encode() + b"\nimport time\nwhile True: time.sleep(0.05)\n# v2\n"
        )
        self.candidate.chmod(0o700)
        self.assertEqual(self._run_crash("before_stop"), 77)
        self.assertTrue(alive(self.old.pid))
        self.assertEqual(digest(self.target), digest(self.candidate))
        self.recover_simulated_next_boot()

    def test_supervisor_crashes_after_old_stopped(self):
        self.candidate.write_bytes(
            b"#!" + sys.executable.encode() + b"\nimport time\nwhile True: time.sleep(0.05)\n# v2\n"
        )
        self.candidate.chmod(0o700)
        self.assertEqual(self._run_crash("before_spawn"), 78)
        self.assertFalse(alive(self.old.pid))
        self.assertEqual((self.dir / "journal").read_text().strip(), "OLD_STOPPED")
        self.recover_simulated_next_boot()


if __name__ == "__main__":
    unittest.main()
