"""Container entrypoint: Alice Pro plus the optional OpenAI Secure MCP Tunnel client.

The tunnel client (openai/tunnel-client) is replaced by a fake executable that
records its argv and environment, so these tests need no network, tunnel ID or
OpenAI key. They check fail-closed configuration, secret separation between the
two processes and supervision (one exits, the other is stopped).
"""

import importlib.util
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "container_entrypoint", ROOT / "scripts" / "container_entrypoint.py"
)
entry = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = entry
_spec.loader.exec_module(entry)

TUNNEL_ID = "tunnel_0123456789abcdef0123456789abcdef"
API_KEY = "sk-test-runtime-key-0123456789"
BEARER = "mcp-bearer-0123456789abcdef0123"

BASE_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "PORT": "8080",
    "ALICE_SHORT_TOKEN": "short-token-secret-value",
    "ALICE_GITHUB_CLIENT_SECRET": "github-client-secret-value",
}
TUNNEL_ENV = {
    "OPENAI_TUNNEL_ID": TUNNEL_ID,
    "OPENAI_TUNNEL_API_KEY": API_KEY,
    "ALICE_MCP_BEARER_TOKEN": BEARER,
    "ALICE_MCP_USER_ID": "maksim",
}


class PlanFailsClosed(unittest.TestCase):
    def test_without_tunnel_settings_only_alice_runs(self):
        plan = entry.plan(dict(BASE_ENV))
        self.assertIsNone(plan.tunnel)
        self.assertEqual(plan.app_env["ALICE_SHORT_TOKEN"], "short-token-secret-value")

    def test_partial_tunnel_settings_refuse_to_start(self):
        for missing in TUNNEL_ENV:
            env = {**BASE_ENV, **TUNNEL_ENV}
            del env[missing]
            with self.subTest(missing=missing), self.assertRaises(entry.TunnelConfigError) as ctx:
                entry.plan(env)
            self.assertIn(missing, str(ctx.exception))
            for secret in (API_KEY, BEARER):
                self.assertNotIn(secret, str(ctx.exception))

    def test_malformed_tunnel_id_is_rejected(self):
        for bad in ("tunnel_", "abc", "tunnel_../x", "tunnel_x y", "tunnel_" + "a" * 200):
            env = {**BASE_ENV, **TUNNEL_ENV, "OPENAI_TUNNEL_ID": bad}
            with self.subTest(bad=bad), self.assertRaises(entry.TunnelConfigError):
                entry.plan(env)

    def test_short_bearer_token_is_rejected(self):
        env = {**BASE_ENV, **TUNNEL_ENV, "ALICE_MCP_BEARER_TOKEN": "short"}
        with self.assertRaises(entry.TunnelConfigError):
            entry.plan(env)

    def test_anonymous_mcp_is_refused_with_a_tunnel(self):
        env = {**BASE_ENV, **TUNNEL_ENV, "ALICE_MCP_ALLOW_ANONYMOUS": "true"}
        with self.assertRaises(entry.TunnelConfigError):
            entry.plan(env)


class PlanSeparatesSecrets(unittest.TestCase):
    def setUp(self):
        self.plan = entry.plan({**BASE_ENV, **TUNNEL_ENV})

    def test_tunnel_targets_local_alice_mcp(self):
        env = self.plan.tunnel.env
        self.assertEqual(env["MCP_SERVER_URL"], "http://127.0.0.1:8080/mcp")
        self.assertEqual(env["CONTROL_PLANE_TUNNEL_ID"], TUNNEL_ID)
        self.assertEqual(env["CONTROL_PLANE_API_KEY"], API_KEY)

    def test_tunnel_injects_alice_bearer_by_env_reference(self):
        env = self.plan.tunnel.env
        name = env["MCP_EXTRA_HEADERS"].split("env:", 1)[1]
        self.assertTrue(env["MCP_EXTRA_HEADERS"].startswith("Authorization: env:"))
        self.assertEqual(env[name], "Bearer " + BEARER)

    def test_no_secret_in_any_argv(self):
        joined = " ".join(self.plan.tunnel.argv + self.plan.app_argv)
        for secret in (API_KEY, BEARER, "short-token-secret-value", "github-client-secret-value"):
            self.assertNotIn(secret, joined)

    def test_tunnel_does_not_receive_alice_app_secrets(self):
        values = set(self.plan.tunnel.env.values())
        self.assertNotIn("short-token-secret-value", values)
        self.assertNotIn("github-client-secret-value", values)
        self.assertNotIn("ALICE_SHORT_TOKEN", self.plan.tunnel.env)

    def test_alice_does_not_receive_the_openai_runtime_key(self):
        self.assertNotIn(API_KEY, set(self.plan.app_env.values()))
        self.assertNotIn("OPENAI_TUNNEL_API_KEY", self.plan.app_env)
        self.assertEqual(self.plan.app_env["ALICE_MCP_BEARER_TOKEN"], BEARER)

    def test_tunnel_health_listener_stays_on_loopback(self):
        argv = self.plan.tunnel.argv
        self.assertEqual(argv[1], "run")
        addr = argv[argv.index("--health.listen-addr") + 1]
        self.assertTrue(addr.startswith("127.0.0.1:"))


def _fake(path: Path, record: Path, exit_after: float | None, code: int = 0) -> Path:
    script = textwrap.dedent(
        f"""\
        #!{sys.executable}
        import json, os, signal, sys, time
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
        with open({str(record)!r}, "w") as fh:
            json.dump({{"argv": sys.argv, "env": dict(os.environ)}}, fh)
        if {exit_after!r} is None:
            while True:
                time.sleep(0.05)
        time.sleep({exit_after or 0!r})
        sys.exit({code})
        """
    )
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Supervision(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="entrypoint-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def run_main(self, env, tunnel_exit, app_exit, app_code=0, tunnel_code=0):
        tunnel = _fake(self.dir / "tunnel", self.dir / "tunnel.json", tunnel_exit, tunnel_code)
        app = _fake(self.dir / "app", self.dir / "app.json", app_exit, app_code)
        started = time.monotonic()
        code = entry.main(environ=env, tunnel_binary=str(tunnel), app_argv=[str(app)])
        return code, time.monotonic() - started

    def record(self, name):
        path = self.dir / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def test_alice_alone_when_tunnel_not_configured(self):
        code, _ = self.run_main(dict(BASE_ENV), tunnel_exit=0, app_exit=0, app_code=0)
        self.assertEqual(code, 0)
        self.assertIsNone(self.record("tunnel"))
        self.assertIsNotNone(self.record("app"))

    def test_without_tunnel_the_container_process_becomes_alice(self):
        """PID 1 must be Alice itself so SIGTERM and exit codes behave as before."""
        with mock.patch.object(entry.os, "execvpe", side_effect=SystemExit(0)) as execvpe:
            with self.assertRaises(SystemExit):
                entry.main(environ=dict(BASE_ENV), app_argv=["python", "app.py"], replace_process=True)
        argv0, argv, env = execvpe.call_args.args
        self.assertEqual((argv0, argv), ("python", ["python", "app.py"]))
        self.assertEqual(env["ALICE_SHORT_TOKEN"], "short-token-secret-value")

    def test_tunnel_exit_stops_alice_and_fails(self):
        env = {**BASE_ENV, **TUNNEL_ENV}
        code, elapsed = self.run_main(env, tunnel_exit=0.3, app_exit=None, tunnel_code=1)
        self.assertNotEqual(code, 0)
        self.assertLess(elapsed, 10)
        tunnel = self.record("tunnel")
        self.assertEqual(tunnel["env"]["CONTROL_PLANE_API_KEY"], API_KEY)
        self.assertNotIn("ALICE_SHORT_TOKEN", tunnel["env"])
        self.assertNotIn("OPENAI_TUNNEL_API_KEY", self.record("app")["env"])

    def test_alice_exit_stops_tunnel_and_fails(self):
        env = {**BASE_ENV, **TUNNEL_ENV}
        code, elapsed = self.run_main(env, tunnel_exit=None, app_exit=0.3, app_code=0)
        self.assertNotEqual(code, 0)
        self.assertLess(elapsed, 10)

    def test_misconfiguration_exits_without_starting_anything(self):
        env = {**BASE_ENV, "OPENAI_TUNNEL_ID": TUNNEL_ID}
        code, _ = self.run_main(env, tunnel_exit=None, app_exit=None)
        self.assertEqual(code, 2)
        self.assertIsNone(self.record("tunnel"))
        self.assertIsNone(self.record("app"))


class DockerfilePinsTunnelClient(unittest.TestCase):
    def test_dockerfile_verifies_pinned_release_and_uses_entrypoint(self):
        text = (ROOT / "Dockerfile").read_text()
        self.assertIn("tunnel-client-runtime-v0.0.14-linux-amd64.zip", text)
        self.assertIn("29d29cf860ada54e4d3c82c715f4fbfcff2abcdc2584c0fc26431308dfa2505b", text)
        self.assertIn("sha256sum -c", text)
        self.assertIn("scripts/container_entrypoint.py", text)


if __name__ == "__main__":
    unittest.main()
