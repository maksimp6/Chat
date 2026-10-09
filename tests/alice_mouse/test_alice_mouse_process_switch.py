import fcntl
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from alice_mouse_process_switch import ProcessSwitch, SwitchConfig, SwitchError, alive, digest


class ProcessSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="alice-switch-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.target = self.base / "mouse"
        self.original = self.base / "original"
        self.candidate = self.base / "candidate"
        self.events = self.base / "events.log"
        self._write_program(self.original, "ORIGINAL")
        self._write_program(self.candidate, "CANDIDATE")
        shutil.copy2(self.original, self.target)
        self.initial = subprocess.Popen(
            [str(self.target), "serve"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._stop_all)
        self.instances = []
        self.addCleanup(self._cleanup_instances)
        self.extra_pids = []
        self._wait_for("ORIGINAL", 1)
        self.cfg = SwitchConfig(
            target=self.target,
            candidate=self.candidate,
            backup=self.original,
            old_pid=self.initial.pid,
            lock=self.base / "transaction.lock",
            journal=self.base / "journal",
            pending=self.base / "cutover.pending",
            timeout=1.2,
        )

    def _write_program(self, dest, role, *, crash=False):
        binary = Path(__file__).with_name("alice_mouse_test_fixture")
        assert binary.is_file(), "build test fixture first"
        import shlex

        # Executable shell trampoline uses exec, preserving real PID/signals.
        # Role/event passed as literal quoted args, never via user input.
        args = [str(binary), str(self.events), role]
        if crash:
            args.append("crash")
        dest.write_text(
            "#!"
            + ("/system/bin/sh" if Path("/system/bin/sh").exists() else "/bin/sh")
            + "\nexec "
            + " ".join(shlex.quote(x) for x in args)
            + "\n"
        )
        dest.chmod(0o700)

    def _wait_for(self, marker, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.events.exists() and marker in self.events.read_text():
                return
            time.sleep(0.02)
        self.fail("no event from process: " + marker)

    def _switch(self):
        obj = ProcessSwitch(self.cfg, external_mouse_active=lambda: False)
        self.instances.append(obj)
        return obj

    def _cleanup_instances(self):
        for obj in self.instances:
            for proc in obj.children.values():
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)

    def _stop_all(self):
        for pid in (self.initial.pid, *self.extra_pids):
            if alive(pid):
                os.kill(pid, 15)
        try:
            self.initial.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.initial.kill()
            self.initial.wait(timeout=2)

    def test_success_uses_real_processes(self):
        switch = self._switch()
        pid = switch.run()
        self.extra_pids.append(pid)
        self._wait_for("CANDIDATE", 1)
        self.assertFalse(alive(self.initial.pid))
        self.assertTrue(alive(pid))
        self.assertEqual(self.cfg.journal.read_text().strip(), "COMMITTED")
        self.assertFalse(self.cfg.pending.exists())
        self.assertEqual(digest(self.target), digest(self.candidate))

    def test_rollback_on_candidate_crash(self):
        self._write_program(self.candidate, "CANDIDATE_CRASH", crash=True)
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "candidate_unhealthy"):
            switch.run()
        self.extra_pids.extend(p for p in [switch.new_pid, switch.restored_pid] if p)
        self.assertEqual(self.cfg.journal.read_text().strip(), "ROLLED_BACK")
        self.assertFalse(self.cfg.pending.exists())
        self.assertEqual(digest(self.target), digest(self.original))
        self.assertFalse(alive(self.initial.pid))
        self.assertTrue(alive(switch.restored_pid))

    def test_rollback_after_install_keeps_original_pid(self):
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "fault_after_install"):
            switch.run(fault="after_install")
        self.assertEqual(self.cfg.journal.read_text().strip(), "ROLLED_BACK")
        self.assertFalse(self.cfg.pending.exists())
        self.assertTrue(alive(self.initial.pid))
        self.assertIsNone(switch.restored_pid)
        self.assertEqual(digest(self.target), digest(self.original))

    def test_rollback_executes_guarded_recovery_image(self):
        guarded = self.base / "guarded"
        self._write_program(guarded, "GUARDED")
        from dataclasses import replace

        self.cfg = replace(self.cfg, rollback_binary=guarded)
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "fault_after_stop"):
            switch.run(fault="after_stop")
        self.extra_pids.append(switch.restored_pid)
        self._wait_for("GUARDED", 1)
        self.assertEqual(digest(self.target), digest(guarded))
        self.assertTrue(alive(switch.restored_pid))

    def test_rollback_after_stopping_original(self):
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "fault_after_stop"):
            switch.run(fault="after_stop")
        self.extra_pids.append(switch.restored_pid)
        self.assertFalse(alive(self.initial.pid))
        self.assertTrue(alive(switch.restored_pid))
        self.assertEqual(digest(self.target), digest(self.original))

    def test_rollback_after_spawn_stops_candidate_first(self):
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "fault_after_spawn"):
            switch.run(fault="after_spawn")
        self.extra_pids.extend([switch.new_pid, switch.restored_pid])
        self.assertFalse(alive(switch.new_pid))
        self.assertTrue(alive(switch.restored_pid))
        self.assertEqual(digest(self.target), digest(self.original))

    def test_external_mouse_blocks_rollback_and_duplicate_start(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, timeout=0.06)
        switch = ProcessSwitch(self.cfg, external_mouse_active=lambda: True)
        self.instances.append(switch)
        with self.assertRaisesRegex(SwitchError, "rollback_refuses_untracked_mouse"):
            switch.run()
        self.assertIsNone(switch.new_pid)
        self.assertIsNone(switch.restored_pid)
        self.assertEqual(self.cfg.journal.read_text().strip(), "MANUAL_RECOVERY_REQUIRED")
        self.assertTrue(self.cfg.pending.exists())

    def test_real_timeout_waits_and_fails_closed(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, timeout=0.35)
        switch = ProcessSwitch(self.cfg, external_mouse_active=lambda: True)
        self.instances.append(switch)
        start = time.monotonic()
        with self.assertRaisesRegex(SwitchError, "rollback_refuses_untracked_mouse"):
            switch.run()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.70)
        self.assertIsNone(switch.new_pid)
        self.assertEqual(self.cfg.journal.read_text().strip(), "MANUAL_RECOVERY_REQUIRED")

    def test_refuses_mismatched_backup(self):
        baseline_digest = digest(self.target)
        self.original.write_text("tampered")
        switch = self._switch()
        with self.assertRaisesRegex(SwitchError, "original_backup_mismatch"):
            switch.run()
        self.assertTrue(alive(self.initial.pid))
        self.assertEqual(digest(self.target), baseline_digest)

    def test_refuses_concurrent_transaction(self):
        with self.cfg.lock.open("a+b") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(SwitchError, "another_transaction_running"):
                self._switch().run()
        self.assertTrue(alive(self.initial.pid))


if __name__ == "__main__":
    unittest.main()
