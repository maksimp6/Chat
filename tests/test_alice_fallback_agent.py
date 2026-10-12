"""Phone-side agent for the #1178 fallback control server.

Runs the real C17 server (see test_alice_c17_tunnel_red.Transport) on loopback
and drives android/fallback_agent/alice_fallback_agent.py against it. No phone,
TLS ingress or RDC is involved; this is mock-agent E2E, not device evidence.
"""

import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.test_alice_c17_tunnel_red import ROOT, Transport

_spec = importlib.util.spec_from_file_location(
    "alice_fallback_agent", ROOT / "android" / "fallback_agent" / "alice_fallback_agent.py"
)
agent_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = agent_mod  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(agent_mod)


class FallbackAgentE2E(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c17-agent-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.server = Transport(self.root / "server")
        self.addCleanup(self.server.stop)
        self.server.start()
        self.token_file = self.root / "agent.token"
        self.token_file.write_text(self.server.agent_token + "\n", encoding="ascii")
        self.token_file.chmod(0o600)
        self.calls = []

    def make_agent(self, handlers=None, state_dir=None):
        config = agent_mod.AgentConfig(
            base_url=self.server.base_url.geturl(),
            token_file=self.token_file,
            state_dir=state_dir or self.root / "agent-state",
            poll_timeout_ms=200,
        )
        if handlers is None:
            handlers = {"noop": lambda payload: self.calls.append(payload)}
        return agent_mod.FallbackAgent(config, handlers=handlers)

    def state(self, command_id):
        status, data, _ = self.server.request("GET", f"/v1/commands/{command_id}", role="admin")
        self.assertEqual(status, 200)
        return data["state"]

    def create(self, command_id, kind="noop", payload=None):
        status, _, _ = self.server.request(
            "POST",
            "/v1/commands",
            role="admin",
            body={"id": command_id, "kind": kind, "payload": payload or {}},
        )
        self.assertIn(status, (201, 202))

    def test_command_is_executed_once_and_acknowledged(self):
        self.create("run-me", payload={"n": 1})
        agent = self.make_agent()
        self.assertEqual(agent.run_once(), 1)
        self.assertEqual(self.calls, [{"n": 1}])
        self.assertEqual(self.state("run-me"), "done")
        self.assertEqual(agent.run_once(), 0)
        self.assertEqual(self.calls, [{"n": 1}])

    def test_unknown_kind_is_acknowledged_failed_without_execution(self):
        self.create("mystery", kind="format-disk")
        agent = self.make_agent()
        self.assertEqual(agent.run_once(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state("mystery"), "failed")

    def test_handler_error_is_acknowledged_failed(self):
        def boom(payload):
            raise RuntimeError("handler failure")

        self.create("breaks", kind="boom")
        agent = self.make_agent(handlers={"boom": boom})
        self.assertEqual(agent.run_once(), 1)
        self.assertEqual(self.state("breaks"), "failed")

    def test_redelivery_after_lost_ack_does_not_execute_again(self):
        """Agent crashed between execution and ACK: the server redelivers after
        restart, the agent must ACK from its own journal, not run it twice."""
        self.create("lost-ack")
        agent = self.make_agent()
        agent.ack = lambda command_id, status: None  # simulate ACK never reaching the server
        self.assertEqual(agent.run_once(), 1)
        self.assertEqual(self.calls, [{}])
        self.server.stop(crash=True)
        self.server.start()
        fresh = self.make_agent()  # same state dir, new process
        fresh.config = agent_mod.AgentConfig(
            base_url=self.server.base_url.geturl(),
            token_file=self.token_file,
            state_dir=self.root / "agent-state",
            poll_timeout_ms=200,
        )
        self.assertEqual(fresh.run_once(), 1)
        self.assertEqual(self.calls, [{}])
        self.assertEqual(self.state("lost-ack"), "done")

    def test_started_but_unfinished_command_is_failed_not_rerun(self):
        self.create("half-done")
        state_dir = self.root / "agent-state"
        agent = self.make_agent(state_dir=state_dir)
        agent.journal.started("half-done")  # crash after start, before result
        self.assertEqual(agent.run_once(), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state("half-done"), "failed")

    def test_waiting_agent_receives_command_created_later(self):
        agent = self.make_agent()
        agent.config = agent_mod.AgentConfig(
            base_url=self.server.base_url.geturl(),
            token_file=self.token_file,
            state_dir=self.root / "agent-state",
            poll_timeout_ms=3000,
        )
        result = {}
        waiter = threading.Thread(target=lambda: result.setdefault("n", agent.run_once()))
        waiter.start()
        threading.Event().wait(0.2)
        self.create("late")
        waiter.join(timeout=5)
        self.assertEqual(result.get("n"), 1)
        self.assertEqual(self.state("late"), "done")

    def test_wrong_token_is_reported_not_retried_as_success(self):
        self.token_file.write_text("x" * 40, encoding="ascii")
        agent = self.make_agent()
        with self.assertRaises(agent_mod.AuthError):
            agent.run_once()

    def test_run_forever_stops_and_backs_off_when_server_is_down(self):
        agent = self.make_agent()
        self.server.stop()
        stop = threading.Event()
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 6:
                stop.set()
            return stop.is_set()

        agent.run_forever(stop, sleep=fake_sleep)
        self.assertEqual(len(sleeps), 6)
        self.assertTrue(all(0 < s <= agent_mod.BACKOFF_MAX_SECONDS for s in sleeps))
        self.assertLess(sleeps[0], sleeps[-1])


class FallbackAgentConfigFailsClosed(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c17-agent-config-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.token = self.root / "agent.token"
        self.token.write_text("agent-" + "z" * 32, encoding="ascii")
        self.token.chmod(0o600)

    def agent(self, url, token=None):
        config = agent_mod.AgentConfig(
            base_url=url, token_file=token or self.token, state_dir=self.root / "state"
        )
        return agent_mod.FallbackAgent(config, handlers={})

    def test_plain_http_is_only_allowed_on_loopback(self):
        for url in ("http://example.com", "http://10.0.0.2:8790", "ftp://127.0.0.1"):
            with self.subTest(url=url), self.assertRaises(agent_mod.ConfigError):
                self.agent(url)
        self.agent("https://control.example.com")
        self.agent("http://127.0.0.1:8790")

    def test_url_with_credentials_or_path_tricks_is_rejected(self):
        for url in ("https://user:pw@control.example.com", "https://control.example.com/x?y"):
            with self.subTest(url=url), self.assertRaises(agent_mod.ConfigError):
                self.agent(url)

    def test_readable_token_file_is_rejected(self):
        self.token.chmod(0o644)
        with self.assertRaises(agent_mod.ConfigError):
            self.agent("https://control.example.com")

    def test_state_dir_is_private(self):
        self.agent("https://control.example.com")
        self.assertEqual(os.stat(self.root / "state").st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
