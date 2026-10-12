"""RED extended #1178 tests: concurrency, crash, hostile HTTP, fuzz and latency.

Requires real C17 server; reuses Transport from test_alice_c17_tunnel_red.
These are deliberately not mocked or xfailed.
"""
import concurrent.futures
import http.client
import json
import random
import socket
import statistics
import tempfile
import time
import unittest
from pathlib import Path
from test_alice_c17_tunnel_red import Transport


class C17ExtendedRED(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c17-extended-red-")
        self.addCleanup(tmp.cleanup)
        self.server = Transport(Path(tmp.name) / "store")
        self.addCleanup(self.server.stop)
        self.server.start()

    def test_storage_private_and_secrets_not_in_errors(self):
        state = self.server.state_dir
        self.assertEqual(state.stat().st_mode & 0o077, 0)
        for name in ("admin.token", "agent.token"):
            self.assertEqual((state / name).stat().st_mode & 0o077, 0)
        status, _, raw = self.server.request("GET", "/v1/commands/missing", role="admin")
        self.assertIn(status, (404, 410))
        self.assertNotIn(self.server.admin_token.encode(), raw)
        self.assertNotIn(self.server.agent_token.encode(), raw)

    def test_concurrent_duplicate_submissions_have_one_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(lambda _: self.server.create("same-id")[0], range(24)))
        self.assertEqual(sum(status in (201, 202) for status in statuses), 1, statuses)
        self.assertTrue(all(status in (201, 202, 409) for status in statuses), statuses)

    def test_concurrent_replayed_ack_has_one_winner(self):
        self.assertIn(self.server.create("ack-race")[0], (201, 202))
        status, data, _ = self.server.request("GET", "/v1/commands/poll?timeout_ms=100", role="agent")
        self.assertEqual(status, 200)
        self.assertIn("ack-race", [command["id"] for command in data["commands"]])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(
                lambda _: self.server.request(
                    "POST", "/v1/commands/ack-race/ack", role="agent",
                    body={"status": "done"},
                )[0], range(12),
            ))
        self.assertEqual(sum(status in (200, 204) for status in statuses), 1, statuses)
        self.assertTrue(all(status in (200, 204, 409, 410) for status in statuses), statuses)

    def test_acked_command_survives_sigkill_without_redelivery(self):
        self.assertIn(self.server.create("acked-before-crash")[0], (201, 202))
        status, data, _ = self.server.request("GET", "/v1/commands/poll?timeout_ms=100", role="agent")
        self.assertEqual(status, 200)
        self.assertIn("acked-before-crash", [command["id"] for command in data["commands"]])
        self.assertIn(self.server.request(
            "POST", "/v1/commands/acked-before-crash/ack", role="agent",
            body={"status": "done"},
        )[0], (200, 204))
        self.server.stop(crash=True)
        self.server.start()
        status, data, _ = self.server.request("GET", "/v1/commands/acked-before-crash", role="admin")
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "done")
        status, data, _ = self.server.request("GET", "/v1/commands/poll?timeout_ms=100", role="agent")
        self.assertIn(status, (200, 204))
        if status == 200:
            self.assertNotIn("acked-before-crash", [command["id"] for command in data["commands"]])

    def test_pending_command_survives_sigkill(self):
        self.assertIn(self.server.create("pending-before-crash")[0], (201, 202))
        self.server.stop(crash=True)
        self.server.start()
        status, data, _ = self.server.request("GET", "/v1/commands/poll?timeout_ms=200", role="agent")
        self.assertEqual(status, 200)
        self.assertIn("pending-before-crash", [command["id"] for command in data["commands"]])

    def test_http_cl_te_request_smuggling_is_rejected(self):
        payload = b'{"id":"smuggled","kind":"noop","payload":{}}'
        request = (
            b"POST /v1/commands HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            + b"Authorization: Bearer " + self.server.admin_token.encode() + b"\r\n"
            + b"Content-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode() + b"\r\n"
            + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + payload
        )
        with socket.create_connection(("127.0.0.1", self.server.base_url.port), timeout=2) as client:
            client.settimeout(2)
            client.sendall(request)
            response = client.recv(4096)
        self.assertTrue(response.startswith(
            (b"HTTP/1.1 400", b"HTTP/1.1 411", b"HTTP/1.1 422")
        ), response)
        status, _, _ = self.server.request("GET", "/v1/commands/smuggled", role="admin")
        self.assertEqual(status, 404)

    def test_seeded_fuzz_corpus_for_invalid_command_ids(self):
        rng = random.Random(1178)
        invalid = ["", "../escape", "..", "%2e%2e%2f", "a/b", "x\x00y", "\r", "\n", "x" * 4096]
        invalid += [
            "".join(chr(rng.randrange(0, 32)) for _ in range(rng.randrange(1, 24)))
            for _ in range(64)
        ]
        for index, command_id in enumerate(invalid):
            with self.subTest(index=index):
                status, _, _ = self.server.create(command_id)
                self.assertIn(status, (400, 413, 422), f"accepted invalid ID: {command_id!r}")
        self.assertIn(self.server.create("valid-after-fuzz")[0], (201, 202))

    def test_agent_cannot_read_admin_command_status(self):
        self.assertIn(self.server.create("private-status")[0], (201, 202))
        status, _, _ = self.server.request("GET", "/v1/commands/private-status", role="agent")
        self.assertEqual(status, 403)

    def test_empty_poll_benchmark_reports_percentiles(self):
        """Steady-state observation, not an invented universal performance threshold."""
        for _ in range(3):
            self.server.request("GET", "/v1/commands/poll?timeout_ms=30", role="agent")
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            status, _, _ = self.server.request(
                "GET", "/v1/commands/poll?timeout_ms=30", role="agent"
            )
            samples.append(1000 * (time.perf_counter() - start))
            self.assertIn(status, (200, 204))
        ordered = sorted(samples)
        print(json.dumps({
            "metric": "c17_empty_poll_steady_state_ms", "warmup": 3,
            "samples": len(ordered), "concurrency": 1,
            "p50": statistics.median(ordered), "p95": ordered[18],
            "p99": ordered[19], "max": ordered[-1],
            "note": "connection setup and TLS must be benchmarked separately",
        }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
