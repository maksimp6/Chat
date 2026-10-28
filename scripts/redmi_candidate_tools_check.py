#!/usr/bin/env python3
"""Test an exact candidate revision on Redmi 9 without replacing the installed app.

Only the evidence manifest, screenshots and video are published. The copied
credential state, private traces and process logs remain inside RUNNER_TEMP.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


REPOSITORY = "maksimp6/Chat"
PORT = 8081
REPLAY_SOURCES = {
    "40446025539": Path(
        "/data/data/com.termux/files/home/github-python-runner/work/"
        "fc911a6a-e4ef-59cc-9398-f692975d603f/"
        ".logos-candidate-tools-nbps7h0e/logos-candidate-tools-sc1uebyg"
    ),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("local evidence endpoint redirected")


class ReplayFinished(Exception):
    """Leave the live-generation branch after an evidence-only replay."""


LOCAL_HTTP = build_opener(ProxyHandler({}), NoRedirect())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(argv, *, cwd=None, env=None, timeout=120, log=None):
    result = subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, timeout=timeout
    )
    if log is not None:
        Path(log).write_bytes(result.stdout + result.stderr)
    require(result.returncode == 0, "candidate command failed: " + Path(argv[0]).name)
    return result.stdout


def private_environment():
    # The candidate server and the tests never inherit the job's write token.
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ACTIONS_", "GH_", "GIT_CONFIG_"))
        and key != "GITHUB_TOKEN"
    }


def production_identity(state):
    path = state / "backend.pid"
    pid = int(path.read_text().strip())
    process = Path("/proc") / str(pid)
    return {
        "pid": pid,
        "start_ticks": process.joinpath("stat")
        .read_text()
        .rsplit(")", 1)[1]
        .split()[19],
        "cwd": str(process.joinpath("cwd").resolve(strict=True)),
    }


def phase(report, name, action):
    entry = {"phase": name, "status": "RUNNING"}
    report["phases"].append(entry)
    try:
        result = action()
    except BaseException as error:
        # Exception text from storage/network libraries may contain private data.
        entry.update(status="FAIL", error_type=type(error).__name__)
        raise
    entry["status"] = (
        result.get("status")
        if isinstance(result, dict)
        and result.get("status") in {"PASS", "FAIL", "NOT_RUN", "PARTIAL", "NOT_PROVEN"}
        else "PASS"
    )
    return result


def make_snapshot(source, destination):
    # Copy bytes from one opened journal inode, never acquire or mutate the live
    # writer's lock. Kristall validates/replays only this private detached copy.
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        remaining = os.fstat(incoming.fileno()).st_size
        while remaining:
            chunk = incoming.read(min(1024 * 1024, remaining))
            require(bool(chunk), "memory snapshot ended unexpectedly")
            outgoing.write(chunk)
            remaining -= len(chunk)


def isolated_state(repo, private, production, executable, env):
    snapshot = private / "source.memory"
    make_snapshot(production / "logos.memory", snapshot)
    shutil.copyfile(
        production / "provider-encryption.key", private / "provider-encryption.key"
    )
    # Do not carry private conversations, attachments or their titles into the
    # browser recording. Keep the existing credential and quota unchanged.
    program = r"""
import os, pathlib, sys
from kristall import Kristall
from provider_accounts.credentials import get_active_credential
from secret_store.credential_crypto import decrypt_secret
private = pathlib.Path(sys.argv[1])
secret = (private / "provider-encryption.key").read_text().strip()
os.environ["LOGOS_PROVIDER_CREDENTIAL_KEY"] = secret
with Kristall(private / "source.memory") as source, Kristall(private / "logos.memory") as target:
    identity = source.get("provider/active/yandex")
    if identity is None:
        raise RuntimeError("Existing Yandex credential unavailable")
    keys = {"provider/active/yandex", "provider/credential/" + str(identity),
            "installation/shared-owner-id", "treasury/next-ledger-id"}
    keys.update(source.items(prefix="quota/"))
    keys.update(source.items(prefix="treasury/account/"))
    with target.transaction() as tx:
        for name in sorted(keys):
            value = source.get(name)
            if value is not None:
                tx.set(name, value)
    get_active_credential(target, decrypt_secret, provider="yandex")
"""
    try:
        command(
            [str(executable), "-c", program, str(private)],
            cwd=repo,
            env=env,
            timeout=90,
            log=private / "snapshot.log",
        )
    finally:
        snapshot.unlink(missing_ok=True)


def wait_ready(process, base, revision):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        require(process.poll() is None, "candidate exited before readiness")
        try:
            with LOCAL_HTTP.open(base + "healthz", timeout=2) as response:
                healthy = (
                    response.status == 200 and json.load(response).get("status") == "ok"
                )
            with LOCAL_HTTP.open(base, timeout=2) as response:
                page = response.read().decode("utf-8")
            with LOCAL_HTTP.open(base + "api/version", timeout=2) as response:
                version = json.load(response)
            if (
                healthy
                and "<title>Logos</title>" in page
                and version.get("runtime_revision") == revision
                and version.get("process_id") == process.pid
            ):
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError("candidate readiness deadline exceeded")


def stop_candidate(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Exactly the Popen child above; never discover or signal by port/name.
            process.kill()
            process.wait(timeout=5)


def historical_trace(private):
    """Read the cited earlier trace independently; never generate a response."""
    invocation_id = "b1fd3b60-4565-4365-bd72-13a52b76c703"
    base = "http://127.0.0.1:8080/api/invocations/" + invocation_id
    result = {
        "status": "UNAVAILABLE",
        "invocation_id": invocation_id,
        "external_charge_verified": False,
    }
    try:
        with LOCAL_HTTP.open(base + "/trace", timeout=10) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
            require(
                len(raw) <= 32 * 1024 * 1024, "historical trace exceeds read budget"
            )
        (private / "historical-trace.json").write_bytes(raw)
        data = json.loads(raw)
        require(isinstance(data, dict), "historical trace is not an object")
        result.update(
            status="READ",
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            executor_tool_calls=len(data["tool_calls"])
            if isinstance(data.get("tool_calls"), list)
            else "UNKNOWN",
        )
        provider = []
        statuses = {
            "completed",
            "incomplete",
            "failed",
            "cancelled",
            "queued",
            "in_progress",
        }
        reasons = {"max_output_tokens", "content_filter"}
        for entry in (
            data.get("responses", []) if isinstance(data.get("responses"), list) else []
        ):
            payload = entry.get("raw") if isinstance(entry, dict) else None
            if not isinstance(payload, dict):
                continue
            output = payload.get("output")
            calls = []
            for item in output if isinstance(output, list) else []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "function_call":
                    calls.append(item)
                elif item.get("type") == "message":
                    calls.extend(
                        part
                        for part in item.get("content", [])
                        if isinstance(part, dict)
                        and part.get("type") == "function_call"
                    )
            detail = payload.get("incomplete_details")
            reason = detail.get("reason") if isinstance(detail, dict) else None
            provider.append(
                {
                    "status": payload.get("status")
                    if payload.get("status") in statuses
                    else "UNKNOWN",
                    "reason": reason if reason in reasons else "UNKNOWN",
                    "raw_function_calls": len(calls)
                    if isinstance(output, list)
                    else "UNKNOWN",
                }
            )
        result["provider_responses"] = provider
        billing = data.get("billing")
        settlement = billing.get("settlement") if isinstance(billing, dict) else None
        settlement_status = (
            settlement.get("status") if isinstance(settlement, dict) else None
        )
        result["treasury_settlement_status"] = (
            settlement_status
            if settlement_status in {"posted", "skipped", "failed", "already_posted"}
            else "UNKNOWN"
        )
    except Exception as error:
        result["status"] = "UNAVAILABLE"
        result["error_type"] = type(error).__name__
    try:
        with LOCAL_HTTP.open(base, timeout=5) as response:
            invocation = json.load(response)
        if isinstance(invocation, dict) and isinstance(
            invocation.get("invocation"), dict
        ):
            invocation = invocation["invocation"]
        value = invocation.get("status") if isinstance(invocation, dict) else None
        result["local_invocation_status"] = (
            value
            if value in {"created", "running", "completed", "failed", "cancelled"}
            else "UNKNOWN"
        )
    except Exception:
        result["local_invocation_status"] = "UNAVAILABLE"
    return result


def independent_file_effects(repo, fixture, executable, env, private):
    """Verify only the known test workspace, never a path supplied by the model."""
    if not isinstance(fixture, dict):
        return {"status": "NOT_RUN", "reason": "fixture_identity_missing"}
    nonce, folder = fixture.get("nonce"), fixture.get("folder")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        return {"status": "FAIL", "reason": "invalid_fixture_nonce"}
    if folder != "tmp/logos-tools-check-" + nonce:
        return {"status": "FAIL", "reason": "unexpected_fixture_folder"}
    program = (
        "import json, pathlib, sys; "
        "from scripts.logos_tools_self_check import verify_file_effects; "
        "print(json.dumps(verify_file_effects(pathlib.Path.cwd(), sys.argv[1], sys.argv[2], extended=sys.argv[3]=='1')))"
    )
    output = command(
        [
            str(executable),
            "-c",
            program,
            nonce,
            folder,
            "1" if fixture.get("extended") is True else "0",
        ],
        cwd=repo,
        env=env,
        timeout=20,
        log=private / "independent-file-effects.log",
    )
    result = json.loads(output)
    require(
        isinstance(result, dict)
        and result.get("status") in {"PASS", "FAIL", "NOT_RUN"},
        "independent file verifier returned invalid evidence",
    )
    return result


def replay_previous_run(run_id, repo, private, executable, env):
    """Inspect the already-paid turn from a detached snapshot; no server/model starts."""
    require(run_id in REPLAY_SOURCES, "replay run is not explicitly allowlisted")
    previous = REPLAY_SOURCES[run_id].resolve(strict=True)
    previous_repo = (previous / "repository").resolve(strict=True)
    previous_report = json.loads(
        (previous / "candidate-check.json").read_text(encoding="utf-8")
    )
    require(
        previous_report.get("candidate_process_stopped") is True,
        "prior run did not confirm its candidate stopped",
    )
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            cwd = (process / "cwd").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        require(cwd != previous_repo, "a prior candidate process is still alive")
    snapshot = private / "replay-source.memory"
    make_snapshot(previous / "private/logos.memory", snapshot)
    snapshot_bytes = snapshot.stat().st_size
    snapshot_hash = hashlib.sha256()
    with snapshot.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            snapshot_hash.update(block)
    replay_env = dict(
        env, LOGOS_MEMORY_PATH=str(snapshot), ALICE_MEMORY_PATH=str(snapshot)
    )
    program = r"""
import hashlib, json, pathlib, re, sys
from agent_memory.client import close_memory_clients
from storage.db import get_store, get_messages
from invocation.manager import get_invocation
from scripts.logos_tools_self_check import evaluate_tool_evidence, verify_file_effects

old_repo, private = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
name_pattern = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
nonce_pattern = re.compile(r"tmp/logos-tools-check-([0-9a-f]{32})")
store = get_store()
# The prior isolated candidate has no copied personal chat history. Search only
# its own test-question records and retrieve only the selected conversation.
questions = [row for row in store.items(prefix="chat/message/").values()
             if isinstance(row, dict) and row.get("role") == "user"
             and nonce_pattern.search(str(row.get("content") or ""))]
if not questions:
    raise RuntimeError("prior paid self-check question unavailable")
selected = max(questions, key=lambda row: int(row["id"]))
question, conversation_id = selected["content"], selected["conversation_id"]
matches = set(nonce_pattern.findall(question))
if len(matches) != 1:
    raise RuntimeError("prior question has ambiguous nonce")
nonce = matches.pop()
folder = "tmp/logos-tools-check-" + nonce
history = get_messages(conversation_id)
position = next(index for index, row in enumerate(history) if row["id"] == selected["id"])
turn = []
for row in history[position:]:
    if turn and row.get("role") == "user":
        break
    trace_ref = row.get("trace") if isinstance(row.get("trace"), dict) else {}
    turn.append({"role": row.get("role"), "text": row.get("text"),
                 "invocation_id": trace_ref.get("invocation_id")})
invocation_ids = sorted({row["invocation_id"] for row in turn
                         if row["role"] == "assistant" and isinstance(row["invocation_id"], str)})
if not invocation_ids:
    raise RuntimeError("prior paid assistant invocation unavailable")
traces, lifecycles, provider_counts, executor_counts = {}, [], [], []
statuses = {"completed", "incomplete", "failed", "cancelled", "queued", "in_progress", "created", "running"}
reasons = {"max_output_tokens", "content_filter"}
for identity in invocation_ids:
    invocation = get_invocation(identity)
    if not isinstance(invocation, dict) or invocation.get("conversation_id") != conversation_id:
        raise RuntimeError("prior invocation does not bind to conversation")
    trace = invocation.get("trace")
    request = trace.get("request") if isinstance(trace, dict) else None
    if not isinstance(request, dict) or request.get("message") != question:
        raise RuntimeError("prior invocation does not bind to exact question")
    traces[identity] = trace
    raw = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (private / ("replayed-trace-" + identity + ".json")).write_bytes(raw)
    lifecycles.append({"invocation_id": identity,
                       "status": invocation.get("status") if invocation.get("status") in statuses else "UNKNOWN",
                       "stored_trace_canonical_sha256": hashlib.sha256(raw).hexdigest(),
                       "stored_trace_canonical_bytes": len(raw)})
    calls = trace.get("tool_calls")
    executor_counts.append(len(calls) if isinstance(calls, list) else "UNKNOWN")
    for entry in trace.get("responses", []) if isinstance(trace.get("responses"), list) else []:
        payload = entry.get("raw") if isinstance(entry, dict) else None
        if not isinstance(payload, dict):
            continue
        output = payload.get("output")
        count = 0
        for item in output if isinstance(output, list) else []:
            if not isinstance(item, dict):
                continue
            count += item.get("type") == "function_call"
            if item.get("type") == "message":
                count += sum(part.get("type") == "function_call" for part in
                             (item.get("content") if isinstance(item.get("content"), list) else [])
                             if isinstance(part, dict))
        detail = payload.get("incomplete_details")
        reason = detail.get("reason") if isinstance(detail, dict) else None
        provider_counts.append({"status": payload.get("status") if payload.get("status") in statuses else "UNKNOWN",
                                "reason": reason if reason in reasons else None,
                                "function_calls": count if isinstance(output, list) else "UNKNOWN"})

marker = "Активный каталог: "
if marker in question:
    names = question.rsplit(marker, 1)[1].strip().split(",")
    if not names or any(not name_pattern.fullmatch(name) for name in names):
        raise RuntimeError("recorded active catalogue malformed")
    inventory_source = "exact_persisted_user_question_active_catalogue"
else:
    names = []
    for trace in traces.values():
        for request in trace.get("api_requests", []):
            payload = request.get("payload") if isinstance(request, dict) else None
            tools = payload.get("tools") if isinstance(payload, dict) else None
            for tool in tools if isinstance(tools, list) else []:
                if isinstance(tool, dict) and tool.get("type") == "function" and name_pattern.fullmatch(str(tool.get("name"))):
                    names.append(tool["name"])
    names = sorted(set(names))
    inventory_source = "recorded_request_offered_functions_only; full_catalogue_unknown"
oracle = evaluate_tool_evidence({"recorded_active": names}, ["recorded_active"], [], turn, traces, question)
effects = verify_file_effects(old_repo, nonce, folder)
summary = {"status": "READ", "new_model_requests": 0, "previous_candidate_process_stopped": True,
           "inventory_source": inventory_source, "lifecycles": lifecycles,
           "provider_responses": provider_counts, "executor_call_counts": executor_counts,
           "tool_evidence": oracle, "independent_file_effects": effects,
           "external_charge_verified": False}
print("REPLAY_SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False))
close_memory_clients()
"""
    output = command(
        [str(executable), "-c", program, str(previous_repo), str(private)],
        cwd=repo,
        env=replay_env,
        timeout=90,
        log=private / "replay.log",
    )
    lines = [
        line
        for line in output.decode("utf-8").splitlines()
        if line.startswith("REPLAY_SUMMARY_JSON=")
    ]
    require(len(lines) == 1, "replay did not return one structured result")
    result = json.loads(lines[0].split("=", 1)[1])
    require(
        isinstance(result, dict) and result.get("status") == "READ",
        "replay evidence unreadable",
    )
    result.update(
        source_run_id=run_id,
        storage_snapshot_sha256=snapshot_hash.hexdigest(),
        storage_snapshot_bytes=snapshot_bytes,
    )
    return result


def write_report(report, directory):
    report_path = directory / "candidate-check.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n### Candidate check on Redmi 9\n\n```json\n")
            stream.write(json.dumps(report, ensure_ascii=False, indent=2))
            stream.write("\n```\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)


def main():
    os.umask(0o077)
    require(
        os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true",
        "candidate check requires a real runner job",
    )
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "unexpected repository")
    for name in (
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "RUNNER_NAME",
        "RUNNER_TEMP",
    ):
        require(bool(os.environ.get(name)), "missing runner context")
    revision = os.environ["GITHUB_SHA"]
    require(re.fullmatch(r"[a-f0-9]{40}", revision), "invalid candidate revision")
    require(
        os.environ.get("GITHUB_REF") == "refs/heads/fix/tools-execution-evidence",
        "candidate check only accepts its review branch",
    )
    model = command(["/system/bin/getprop", "ro.product.model"]).decode().strip()
    require(model == "Redmi 9", "this job requires Redmi 9")
    production_app = Path.home() / "logos-local"
    production_state = Path.home() / "logos-local-state"
    executable = production_app / ".venv/bin/python"
    require(executable.is_file(), "installed native Python unavailable")
    native = list(
        (production_state / "native-deps").glob("**/site-packages/cryptography")
    )
    require(len(native) == 1, "installed native dependencies unavailable")
    initial_identity = production_identity(production_state)
    run_root = Path(
        tempfile.mkdtemp(prefix="logos-candidate-tools-", dir=os.environ["RUNNER_TEMP"])
    )
    private = run_root / "private"
    private.mkdir(mode=0o700)
    repo = run_root / "repository"
    repo.mkdir(mode=0o700)
    report = {
        "status": "RUNNING",
        "candidate_revision": revision,
        "device_model": model,
        "test_url": "http://127.0.0.1:8081/",
        "production_process_unchanged": False,
        "phases": [],
        "model_evidence": "NOT_RUN",
        "external_charge_verified": False,
    }
    environment = private_environment()
    environment.update(
        GIT_LFS_SKIP_SMUDGE="1",
        GIT_TERMINAL_PROMPT="0",
        PYTHONPATH=str(native[0].parent),
        LOGOS_LOG_DIR=str(private / "logs"),
    )
    process = None
    failed = False
    try:

        def checkout():
            command(["git", "init", "--quiet"], cwd=repo, env=environment)
            command(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/" + REPOSITORY + ".git",
                ],
                cwd=repo,
                env=environment,
            )
            command(
                [
                    "git",
                    "fetch",
                    "--quiet",
                    "--depth=1",
                    "--filter=blob:none",
                    "origin",
                    revision,
                ],
                cwd=repo,
                env=environment,
                timeout=180,
                log=private / "fetch.log",
            )
            command(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "checkout",
                    "--quiet",
                    "--detach",
                    "FETCH_HEAD",
                ],
                cwd=repo,
                env=environment,
                timeout=180,
                log=private / "checkout.log",
            )
            actual = (
                command(["git", "rev-parse", "HEAD"], cwd=repo, env=environment)
                .decode()
                .strip()
            )
            require(actual == revision, "candidate checkout revision differs")

        phase(report, "exact_candidate_checkout", checkout)
        report["historical_trace"] = historical_trace(private)

        def regressions():
            # These tests replace only provider transport, proving the status
            # regression without spending on a model. Browser evidence follows.
            result = subprocess.run(
                [
                    str(executable),
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_provider_terminal_status.py",
                    "tests/test_logos_tools_self_check.py",
                    "tests/test_redmi_rdp_long_string.py",
                    "tests/test_tool_inventory_receipt.py",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                timeout=120,
            )
            (private / "regressions.log").write_bytes(result.stdout + result.stderr)
            print(
                (result.stdout + result.stderr).decode("utf-8", "replace")[-50000:],
                flush=True,
            )
            require(result.returncode == 0, "candidate regression tests failed")

        phase(report, "terminal_status_and_evidence_regressions", regressions)
        node = shutil.which("node")
        if node:

            def javascript_regressions():
                for index, arguments in enumerate(
                    (
                        ["tests/test_trace_summary.js"],
                        ["--test", "tests/test_ui_trace_summary.js"],
                    )
                ):
                    output = command(
                        [node, *arguments],
                        cwd=repo,
                        env=environment,
                        timeout=60,
                        log=private / ("javascript-regression-" + str(index) + ".log"),
                    )
                    print(output.decode("utf-8", "replace")[-20000:], flush=True)

            phase(
                report, "trace_summary_javascript_regressions", javascript_regressions
            )
        else:
            report["phases"].append(
                {
                    "phase": "trace_summary_javascript_regressions",
                    "status": "NOT_RUN",
                    "reason": "installed_node_unavailable",
                }
            )
        replay_run = os.environ.get("LOGOS_REPLAY_RUN")
        if replay_run:
            report["replay"] = phase(
                report,
                "replay_already_paid_browser_turn",
                lambda: replay_previous_run(
                    replay_run, repo, private, executable, environment
                ),
            )
            report["new_model_requests"] = 0
            report["model_evidence"] = report["replay"]["tool_evidence"].get(
                "status", "NOT_RUN"
            )
            report["tool_coverage"] = report["replay"]["tool_evidence"].get(
                "coverage", {}
            )
            report["independent_file_effects"] = report["replay"][
                "independent_file_effects"
            ]
            report["status"] = (
                "PASS"
                if (
                    report["tool_coverage"].get("status") == "PASS"
                    and report["independent_file_effects"].get("status") == "PASS"
                )
                else "FAIL"
            )
            failed = report["status"] != "PASS"
            raise ReplayFinished()
        phase(
            report,
            "isolated_private_credential_snapshot",
            lambda: isolated_state(
                repo, private, production_state, executable, environment
            ),
        )

        with socket.socket() as port_check:
            require(
                port_check.connect_ex(("127.0.0.1", PORT)) != 0,
                "candidate port already occupied",
            )
        # Preserve job context for the driver; strip it from the application.
        server_env = {
            key: value
            for key, value in environment.items()
            if not key.startswith(("GITHUB_", "RUNNER_", "ACTIONS_", "GH_"))
        }
        secret = (private / "provider-encryption.key").read_text().strip()
        server_env.update(
            HOST="127.0.0.1",
            PORT=str(PORT),
            FLASK_DEBUG="0",
            LOGOS_MEMORY_PATH=str(private / "logos.memory"),
            ALICE_MEMORY_PATH=str(private / "logos.memory"),
            LOGOS_PROVIDER_CREDENTIAL_KEY=secret,
            ALICE_PROVIDER_CREDENTIAL_KEY=secret,
            LOGOS_APP_REVISION=revision,
            LOGOS_PROJECT_ROOT=str(repo),
            LOGOS_LOCAL_REPO_DIR=str(repo),
        )
        with (private / "backend.log").open("wb") as log:
            process = subprocess.Popen(
                [str(executable), "app.py"],
                cwd=repo,
                env=server_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        base = report["test_url"]
        phase(
            report, "candidate_readiness", lambda: wait_ready(process, base, revision)
        )
        capture_env = os.environ.copy()
        capture_env.update(
            LOGOS_TOOLS_SELF_CHECK="1",
            LOGOS_CAPTURE_URL=base,
            RUNNER_TEMP=str(run_root),
            TMPDIR=str(run_root),
        )
        capture_dir = run_root / (
            "logos-screenshots-"
            + os.environ["GITHUB_RUN_ID"]
            + "-"
            + os.environ["GITHUB_RUN_ATTEMPT"]
        )
        try:
            phase(
                report,
                "real_browser_capture",
                lambda: command(
                    [
                        str(executable),
                        "scripts/redmi_screenshot_capture.py",
                        str(capture_dir),
                    ],
                    cwd=repo,
                    env=capture_env,
                    timeout=330,
                    log=private / "capture.log",
                ),
            )
        except Exception:
            failed = True
        manifest_path = capture_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            scenario = manifest.get("creation_scenario", {})
            check = scenario.get("checkpoints", {}).get("tools_self_check", {})
            report["model_evidence"] = check.get("status", "NOT_RUN")
            report["tool_coverage"] = check.get("coverage", {})
            stop_candidate(process)
            report["candidate_process_stopped"] = process.poll() is not None
            report["independent_file_effects"] = phase(
                report,
                "independent_file_effects",
                lambda: independent_file_effects(
                    repo, check.get("file_fixture"), executable, environment, private
                ),
            )
            report["production_process_unchanged"] = (
                production_identity(production_state) == initial_identity
            )
            report["status"] = (
                "PASS"
                if (
                    not failed
                    and check.get("status") == "PASS"
                    and scenario.get("verdict") == "PASS"
                    and report["independent_file_effects"].get("status") == "PASS"
                    and report["production_process_unchanged"]
                )
                else "FAIL"
            )
            manifest["candidate_check"] = report
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            phase(
                report,
                "publish_and_verify_browser_evidence",
                lambda: command(
                    [
                        str(executable),
                        "scripts/publish_redmi_screenshots.py",
                        str(capture_dir),
                    ],
                    cwd=repo,
                    env=capture_env,
                    timeout=300,
                    log=private / "publication.log",
                ),
            )
            print((private / "publication.log").read_text(encoding="utf-8"), flush=True)
            require(
                check.get("status") == "PASS"
                and scenario.get("verdict") == "PASS"
                and report["independent_file_effects"].get("status") == "PASS",
                "live tool coverage not fully proven",
            )
        else:
            raise RuntimeError("browser evidence manifest unavailable")
        require(not failed, "capture failed after preserving available evidence")
        report["status"] = "PASS"
    except ReplayFinished:
        pass
    except Exception:
        failed = True
        report["status"] = "FAIL"
    finally:
        stop_candidate(process)
        report["candidate_process_stopped"] = (
            process is None or process.poll() is not None
        )
        try:
            report["production_process_unchanged"] = (
                production_identity(production_state) == initial_identity
            )
        except (OSError, ValueError):
            report["production_process_unchanged"] = False
        if not report["production_process_unchanged"]:
            failed = True
            report["status"] = "FAIL"
        write_report(report, run_root)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(error).__name__,
                    "detail": "candidate setup failed; private diagnostics withheld",
                }
            ),
            flush=True,
        )
        raise SystemExit(1)
