"""Startup, journal-recovery and long-poll checks for the #1178 C17 server.

Complements the black-box contract in test_alice_c17_tunnel_red.py with the
fail-closed startup rules and journal recovery documented in
deploy/fallback-control/README.md. Loopback only; no phone or TLS.
"""

import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.test_alice_c17_tunnel_red import BINARY, Transport


def _run_once(args, timeout=5):
    return subprocess.run([str(BINARY), *args], capture_output=True, timeout=timeout, check=False)


class C17StartupFailsClosed(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c17-startup-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "store"
        self.state.mkdir(mode=0o700)
        self.admin = self.state / "admin.token"
        self.agent = self.state / "agent.token"
        self.admin.write_text("startup-admin-" + "a" * 32, encoding="ascii")
        self.agent.write_text("startup-agent-" + "b" * 32, encoding="ascii")
        self.admin.chmod(0o600)
        self.agent.chmod(0o600)

    def args(self, listen="127.0.0.1:0"):
        return [
            "--listen",
            listen,
            "--state-dir",
            str(self.state),
            "--ready-file",
            str(self.root / "ready.json"),
            "--admin-token-file",
            str(self.admin),
            "--agent-token-file",
            str(self.agent),
        ]

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "ready.json").exists())
        for token in (self.admin.read_bytes(), self.agent.read_bytes()):
            self.assertNotIn(token, result.stderr)

    def test_non_loopback_listener_is_refused(self):
        for listen in ("0.0.0.0:0", "192.0.2.1:0", "localhost:0", "[::]:0"):
            with self.subTest(listen=listen):
                self.assert_refused(_run_once(self.args(listen)))

    def test_readable_token_file_is_refused(self):
        self.admin.chmod(0o644)
        self.assert_refused(_run_once(self.args()))

    def test_short_or_shared_tokens_are_refused(self):
        self.admin.write_text("Zq9!x", encoding="ascii")
        self.assert_refused(_run_once(self.args()))
        self.admin.write_text(self.agent.read_text(encoding="ascii"), encoding="ascii")
        self.assert_refused(_run_once(self.args()))

    def test_shared_state_directory_is_refused(self):
        self.state.chmod(0o755)
        self.assert_refused(_run_once(self.args()))

    def test_corrupt_journal_record_is_refused(self):
        server = Transport(self.state)
        server.start()
        self.assertIn(server.create("kept")[0], (201, 202))
        server.stop()
        journal = self.state / "journal"
        data = bytearray(journal.read_bytes())
        data[-3] = ord("0") if data[-3] != ord("0") else ord("1")  # break the CRC
        journal.write_bytes(bytes(data))
        self.assert_refused(_run_once(self.args()))


class C17JournalRecovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c17-journal-")
        self.addCleanup(tmp.cleanup)
        self.server = Transport(Path(tmp.name) / "store")
        self.addCleanup(self.server.stop)
        self.server.start()

    def test_torn_tail_is_truncated_and_earlier_records_survive(self):
        self.assertIn(self.server.create("before-tear")[0], (201, 202))
        self.server.stop(crash=True)
        journal = self.server.state_dir / "journal"
        intact = journal.read_bytes()
        with journal.open("ab") as handle:
            handle.write(b"R1 C 10:torn-recor")
        self.server.start()
        self.assertEqual(journal.read_bytes(), intact)
        status, data, _ = self.server.request("GET", "/v1/commands/before-tear", role="admin")
        self.assertEqual((status, data["state"]), (200, "queued"))
        self.assertIn(self.server.create("after-tear")[0], (201, 202))
        self.server.stop(crash=True)
        self.server.start()
        self.assertEqual(self.server.create("after-tear")[0], 409)

    def test_second_server_on_same_store_is_refused(self):
        result = _run_once(
            [
                "--listen",
                "127.0.0.1:0",
                "--state-dir",
                str(self.server.state_dir),
                "--ready-file",
                str(self.server.state_dir / "second-ready.json"),
                "--admin-token-file",
                str(self.server.state_dir / "admin.token"),
                "--agent-token-file",
                str(self.server.state_dir / "agent.token"),
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.server.state_dir / "second-ready.json").exists())

    def test_failed_ack_is_durable_and_terminal(self):
        self.assertIn(self.server.create("will-fail")[0], (201, 202))
        status, _, _ = self.server.request(
            "POST", "/v1/commands/will-fail/ack", role="agent", body={"status": "failed"}
        )
        self.assertIn(status, (200, 204))
        self.server.stop(crash=True)
        self.server.start()
        status, data, _ = self.server.request("GET", "/v1/commands/will-fail", role="admin")
        self.assertEqual((status, data["state"]), (200, "failed"))
        status, _, _ = self.server.request(
            "POST", "/v1/commands/will-fail/ack", role="agent", body={"status": "done"}
        )
        self.assertEqual(status, 409)

    def test_unknown_ack_status_and_unknown_command_are_rejected(self):
        self.assertIn(self.server.create("strict-ack")[0], (201, 202))
        status, _, _ = self.server.request(
            "POST", "/v1/commands/strict-ack/ack", role="agent", body={"status": "maybe"}
        )
        self.assertEqual(status, 422)
        status, _, _ = self.server.request(
            "POST", "/v1/commands/never-created/ack", role="agent", body={"status": "done"}
        )
        self.assertEqual(status, 404)

    def test_waiting_poll_is_woken_by_new_command(self):
        result = {}

        def poll():
            start = time.monotonic()
            result["response"] = self.server.request(
                "GET", "/v1/commands/poll?timeout_ms=3000", role="agent", timeout=5
            )
            result["elapsed"] = time.monotonic() - start

        waiter = threading.Thread(target=poll)
        waiter.start()
        time.sleep(0.2)
        self.assertIn(self.server.create("wakes-poll")[0], (201, 202))
        waiter.join(timeout=5)
        status, data, _ = result["response"]
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in data["commands"]], ["wakes-poll"])
        self.assertLess(result["elapsed"], 2.0)

    def test_poll_timeout_above_bound_is_rejected(self):
        status, _, _ = self.server.request(
            "GET", "/v1/commands/poll?timeout_ms=30001", role="agent"
        )
        self.assertEqual(status, 400)

    def test_graceful_stop_removes_ready_file(self):
        ready = self.server.state_dir / "ready.json"
        proc = self.server.process
        self.server.stop()
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(ready.exists())


if __name__ == "__main__":
    unittest.main()
