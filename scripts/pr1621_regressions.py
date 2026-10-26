"""Offline PR regressions on the existing physical Redmi 9 Actions runner.

This does not deploy the PR or establish live provider/browser/chat E2E.
No dependencies are installed, no production state/key is opened, and all
generated state and evidence stay inside this job's RUNNER_TEMP.
"""

from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
MAX_LOG_BYTES = 1024 * 1024
GROUP_TIMEOUT_SECONDS = 60
SYNTHETIC_KEY = "MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE="

GROUPS = (
    ("core-files-runner", (
        "tests/test_trace_security.py", "tests/test_system_log.py",
        "tests/test_redmi_runner_safety.py", "tests/test_tool_providers_safety.py",
        "tests/test_browser_safety.py", "tests/test_printing3d_gcode_edges.py",
    )),
    ("credentials", (
        "tests/test_provider_credentials.py", "tests/test_provider_credentials_routes.py",
    )),
    ("messages-images-voice", (
        "tests/test_chat_provider_conversation.py", "tests/test_image_blobs.py",
        "tests/test_voice_routes.py", "tests/test_logos_client_paths.py",
        "tests/test_chat_no_auth.py",
    )),
    ("agent-delivery", (
        "tests/test_agent_runtime_delivery.py", "tests/test_admin_no_auth.py",
    )),
    ("environment-lifecycle", ("tests/test_environment_runtime_failures.py",)),
)


PYTHON_GUARD = r'''
import ipaddress
import socket

def permitted(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo

def connect(self, address):
    if not permitted(address):
        raise RuntimeError("PR1621_EXTERNAL_NETWORK_BLOCKED")
    return _connect(self, address)

def connect_ex(self, address):
    if not permitted(address):
        raise RuntimeError("PR1621_EXTERNAL_NETWORK_BLOCKED")
    return _connect_ex(self, address)

def getaddrinfo(host, port, *args, **kwargs):
    if not permitted((host, port)):
        raise RuntimeError("PR1621_EXTERNAL_NETWORK_BLOCKED")
    return _getaddrinfo(host, port, *args, **kwargs)

socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo
'''

PYTEST_ISOLATION = r'''
import pytest

@pytest.fixture(autouse=True)
def pr1621_private_state(tmp_path, monkeypatch):
    from agent_memory.client import close_memory_clients
    close_memory_clients()
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    monkeypatch.setenv("LOGOS_ENV_RUNTIME_ROOT", str(tmp_path / "environments"))
    monkeypatch.setenv("LOGOS_IMAGE_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("LOGOS_LOG_DIR", str(tmp_path / "logs"))
    yield
    close_memory_clients()
'''

NODE_GUARD = r'''
"use strict";
const net = require("node:net");
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  let host;
  if (typeof args[0] === "object" && args[0] !== null) {
    if (args[0].path) throw new Error("PR1621_EXTERNAL_NETWORK_BLOCKED");
    host = args[0].host || "localhost";
  } else if (typeof args[0] === "number") {
    host = typeof args[1] === "string" ? args[1] : "localhost";
  }
  if (host !== "localhost" && host !== "::1" && !/^127\./.test(host || "")) {
    throw new Error("PR1621_EXTERNAL_NETWORK_BLOCKED");
  }
  return connect.apply(this, args);
};
globalThis.fetch = async function () {
  throw new Error("PR1621_EXTERNAL_NETWORK_BLOCKED");
};
'''


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    return path


def run_command(command: list[str], directory: Path, env: dict[str, str], log: Path,
                timeout: float = GROUP_TIMEOUT_SECONDS) -> dict:
    """Bound time and saved output; preserve the real child exit code."""
    started = time.monotonic()
    saved = discarded = 0
    timed_out = False
    process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    with log.open("wb") as output, selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = started + timeout
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                keep = chunk[:max(0, MAX_LOG_BYTES - saved)]
                output.write(keep)
                saved += len(keep)
                discarded += len(chunk) - len(keep)
        if timed_out:
            output.write(b"\nPR1621_COMMAND_TIMEOUT\n")
        if discarded:
            output.write(b"\nPR1621_LOG_TRUNCATED\n")
    process.stdout.close()
    code = process.wait(timeout=5)
    return {
        "command": command, "exit_code": 124 if timed_out else code,
        "timed_out": timed_out, "seconds": round(time.monotonic() - started, 3),
        "log": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "log_discarded_bytes": discarded,
    }


def junit_counts(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    return {key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def child_environment(harness: Path, state: Path, native: list[str]) -> dict[str, str]:
    # Copy only named OS/job context. Provider/token values are never inspected,
    # logged or inherited by test processes. HOME remains the real runner HOME.
    allowed = (
        "HOME", "PATH", "PREFIX", "LD_PRELOAD", "LANG", "ANDROID_ROOT", "ANDROID_DATA",
        "GITHUB_ACTIONS", "CI", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA",
        "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_WORKSPACE",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "PYTHONPATH": os.pathsep.join([str(harness), str(ROOT), *native]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1", "TMPDIR": str(state),
        "LOGOS_MEMORY_PATH": str(state / "bootstrap.memory"),
        "LOGOS_ENV_RUNTIME_ROOT": str(state / "environments"),
        "LOGOS_IMAGE_DIR": str(state / "images"), "LOGOS_LOG_DIR": str(state / "logs"),
        "LOGOS_PROVIDER_CREDENTIAL_KEY": SYNTHETIC_KEY,
        "LOGOS_KEY_MANAGER_KEY": SYNTHETIC_KEY,
        "LOGOS_APP_REVISION": os.environ["GITHUB_SHA"],
    })
    return env


def print_failure_tail(result: dict) -> None:
    print("PR1621_FAILURE_LOG " + result["log"], flush=True)
    print(Path(result["log"]).read_bytes()[-16000:].decode("utf-8", errors="replace"), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New evidence directory inside RUNNER_TEMP")
    parser.add_argument("--focus", choices=("all", "stl_readiness"), default="all")
    args = parser.parse_args()
    selected_groups = GROUPS if args.focus == "all" else (("stl-readiness-followup", (
        "tests/test_printing3d_gcode_edges.py::test_readiness_rejects_unknown_model_and_records_evidence",
        "tests/test_printing3d_gcode_edges.py::test_readiness_operator_gate_requires_every_other_check",
    )),)
    required = ("GITHUB_RUN_ID", "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_SHA")
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("CI") != "true"
            or any(not os.environ.get(name) for name in required)):
        raise SystemExit("LOCAL_TESTS_FORBIDDEN: real GitHub Actions runner context required")
    model = subprocess.check_output(["/system/bin/getprop", "ro.product.model"], text=True).strip()
    if model != "Redmi 9":
        raise SystemExit("WRONG_DEVICE: this validation requires Redmi 9")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha != os.environ["GITHUB_SHA"]:
        raise SystemExit("SOURCE_REVISION_MISMATCH")
    if ROOT == (Path.home() / "logos-local").resolve():
        raise SystemExit("ISOLATED_CHECKOUT_REQUIRED")
    os.umask(0o077)
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    evidence_path = (args.out or runner_temp / "pr1621-regressions").resolve()
    if not ROOT.is_relative_to(runner_temp) or not evidence_path.is_relative_to(runner_temp):
        raise SystemExit("ISOLATED_JOB_PATH_REQUIRED: source and output must be under RUNNER_TEMP")
    evidence = private_directory(evidence_path)
    harness = private_directory(evidence / "harness")
    (harness / "sitecustomize.py").write_text(PYTHON_GUARD, encoding="utf-8")
    (harness / "pr1621_isolation.py").write_text(PYTEST_ISOLATION, encoding="utf-8")
    (harness / "node-guard.cjs").write_text(NODE_GUARD, encoding="utf-8")
    native_root = Path.home() / "logos-local-state" / "native-deps"
    native = [str(path) for path in native_root.glob("**/site-packages")
              if (path / "cryptography").is_dir()]
    report = {
        "scope": "offline_regressions_on_physical_runner_not_live_e2e",
        "focus": args.focus,
        "source_sha": sha, "device_model": model,
        "run_id": os.environ["GITHUB_RUN_ID"], "runner_name": os.environ["RUNNER_NAME"],
        "free_bytes_before": shutil.disk_usage(evidence).free,
        "groups": [], "live_provider_requests": "blocked_by_network_guard",
        "production_deployment": "NOT_RUN",
    }
    report_path = evidence / "report.json"

    def save() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    # Deliberate failure checks the harness exit-code/JUnit path; it is not a
    # product success scenario and is never added to the product pass count.
    negative = private_directory(evidence / "negative-control")
    (negative / "test_expected_failure.py").write_text(
        '''def test_harness_detects_failure():
    import socket
    assert socket.socket.connect.__module__ == "sitecustomize", "network guard not loaded"
    assert socket.getaddrinfo.__module__ == "sitecustomize", "DNS guard not loaded"
    assert False, "EXPECTED_NEGATIVE_CONTROL"
''',
        encoding="utf-8",
    )
    negative_junit = negative / "junit.xml"
    result = run_command(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml", str(negative_junit),
         str(negative / "test_expected_failure.py")], ROOT,
        child_environment(harness, negative, native), negative / "output.log", timeout=20,
    )
    try:
        counts = junit_counts(negative_junit)
    except (OSError, ValueError, ET.ParseError):
        counts = {}
    try:
        expected_failure = any(
            "EXPECTED_NEGATIVE_CONTROL" in failure.get("message", "")
            for failure in ET.parse(negative_junit).iter("failure")
        )
    except (OSError, ValueError, ET.ParseError):
        expected_failure = False
    result.update(counts=counts, verdict="PASS" if result["exit_code"] == 1 and expected_failure and counts == {
        "tests": 1, "failures": 1, "errors": 0, "skipped": 0,
    } else "FAIL")
    report["negative_control"] = result
    save()
    if result["verdict"] != "PASS":
        report["verdict"] = "FAIL"
        report["groups"] = [{"name": name, "verdict": "NOT_RUN", "reason": "negative_control_failed"}
                            for name, _ in selected_groups]
        if args.focus == "all":
            report["groups"].append({"name": "javascript", "verdict": "NOT_RUN", "reason": "negative_control_failed"})
        save()
        print_failure_tail(result)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 1
    for name, tests in selected_groups:
        group = private_directory(evidence / name)
        junit = group / "junit.xml"
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "pr1621_isolation",
                   "--basetemp", str(group / "pytest-state"), "--junitxml", str(junit), *tests]
        result = run_command(command, ROOT, child_environment(harness, group, native), group / "output.log")
        try:
            counts = junit_counts(junit)
            passed = result["exit_code"] == 0 and counts["tests"] > 0 and not any(
                counts[key] for key in ("failures", "errors", "skipped"))
        except (OSError, ValueError, ET.ParseError):
            counts, passed = {}, False
        result.update(name=name, counts=counts, junit=str(junit), verdict="PASS" if passed else "FAIL")
        report["groups"].append(result)
        save()
        print(json.dumps({"group": name, "verdict": result["verdict"], "counts": counts}), flush=True)
        if not passed:
            print_failure_tail(result)
    node_group = private_directory(evidence / "javascript")
    node = shutil.which("node")
    node_results = []
    if node and args.focus == "all":
        for name in ("test_browser_emulator.js", "test_voice_recording.js"):
            node_results.append(run_command(
                [node, "--require", str(harness / "node-guard.cjs"), str(ROOT / "tests" / name)],
                ROOT, child_environment(harness, node_group, native), node_group / (name + ".log"), timeout=30,
            ))
            if node_results[-1]["exit_code"] != 0:
                print_failure_tail(node_results[-1])
    if args.focus == "all":
        report["groups"].append({
            "name": "javascript", "commands": node_results,
            "verdict": "PASS" if len(node_results) == 2 and all(x["exit_code"] == 0 for x in node_results) else "FAIL",
            "reason": None if node else "node_not_installed_no_install_attempted",
        })
    report["verdict"] = "PASS" if (report["negative_control"]["verdict"] == "PASS" and all(
        group["verdict"] == "PASS" for group in report["groups"])) else "FAIL"
    report["free_bytes_after"] = shutil.disk_usage(evidence).free
    save()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
