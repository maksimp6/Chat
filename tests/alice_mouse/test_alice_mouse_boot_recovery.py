import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).with_name("alice_mouse_watchdog_v2.sh"))


class BootRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="alice-boot-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "staging").mkdir()
        self.backup = self.root / "staging/rollback-guard"
        self.backup.write_bytes(b"guarded-original")
        self.target = self.root / "mouse"
        self.target.write_bytes(b"locked-candidate")
        self.pending = self.root / "cutover.pending"
        self.pending.write_text("previous-boot-id\n")

    def run_once(self):
        env = os.environ.copy()
        env.update(ALICE_MOUSE_TEST_MODE="1", ALICE_MOUSE_ROOT=str(self.root))
        return subprocess.run(["/system/bin/sh" if Path("/system/bin/sh").exists() else "/bin/sh", SCRIPT],
                              env=env, capture_output=True, text=True,
                              timeout=3)

    def test_recovers_stale_pending(self):
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), b"guarded-original")
        self.assertFalse(self.pending.exists())

    def test_refuses_restore_while_mouse_active(self):
        (self.root / "synthetic_active").touch()
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"locked-candidate")
        self.assertTrue(self.pending.exists())

    def test_missing_backup_fail_closed(self):
        self.backup.unlink()
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"locked-candidate")
        self.assertTrue(self.pending.exists())

    def test_current_boot_fresh_pending_does_not_interfere(self):
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.pending.write_text(boot_id + "\n")
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"locked-candidate")
        self.assertTrue(self.pending.exists())

    def test_current_boot_stale_recovers(self):
        import time
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.pending.write_text(boot_id + "\n")
        old = time.time() - 61
        os.utime(self.pending, (old, old))
        self.assertEqual(self.run_once().returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"guarded-original")

    def test_no_pending_leaves_candidate_alone(self):
        self.pending.unlink()
        result = self.run_once()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"locked-candidate")

    def test_recovery_idempotent(self):
        self.assertEqual(self.run_once().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"guarded-original")


if __name__ == "__main__":
    unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
