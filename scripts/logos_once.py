"""One visible Actions step around the existing native Logos entry points.

The court hold remains authoritative. This wrapper does not provide an override,
change a model profile, or retry a failed command or provider request.
"""

from __future__ import annotations
import os

import base64
import hashlib
import html
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def run_dialogue_command(
    command: list[str], root: Path, environ: dict[str, str], answer: Path
) -> None:
    """Export native checkpoints while polling occurs inside the existing process."""
    fingerprint = None
    with (
        tempfile.TemporaryFile() as diagnostics,
        subprocess.Popen(
            command, cwd=root, env=environ, stdout=diagnostics, stderr=subprocess.STDOUT
        ) as process,
    ):
        while True:
            exit_code = process.poll()
            try:
                payload = answer.read_bytes()
                checkpoint = json.loads(payload)
                digest = hashlib.sha256(payload).hexdigest()
                if isinstance(checkpoint, dict) and digest != fingerprint:
                    emit_result(checkpoint, None, marker="LOGOS_CHECKPOINT")
                    fingerprint = digest
            except (OSError, ValueError):
                pass
            if exit_code is not None:
                if exit_code:
                    raise subprocess.CalledProcessError(exit_code, command)
                return
            time.sleep(1)


def run_native(
    root: Path, temporary: Path, environ: dict[str, str]
) -> tuple[dict[str, Any], int]:
    """Check admission before dependencies/context/provider work, then reuse native CLIs."""
    result: dict[str, Any] = {
        "status": "failed",
        "reply_state": "unknown",
        "response_ids": [],
    }
    phase = "input"
    answer = temporary / "logos-answer.json"
    try:
        question = environ.get("LOGOS_QUESTION", "")
        issue = environ.get("LOGOS_CASE_ISSUE", "1413")
        pr = environ.get("LOGOS_CASE_PR", "1435")
        if not question.strip() or not all(
            re.fullmatch(r"[1-9][0-9]*", x) for x in (issue, pr)
        ):
            raise ValueError("A question and positive case IDs are required")

        phase = "admission"
        from scripts.logos_generation_gate import generation_allowed
        from scripts.logos_launch_hold import (
            NativeLogosLaunchBlocked,
            enforce_native_launch_hold,
            hold_receipt,
        )

        try:
            enforce_native_launch_hold()
        except NativeLogosLaunchBlocked:
            result.update(
                status="blocked",
                error="NativeLogosLaunchBlocked",
                launch_hold=hold_receipt(),
                generation_requests=0,
            )
            return result, 1
        if not generation_allowed(environ):
            result.update(
                status="blocked", error="GenerationNotPermitted", generation_requests=0
            )
            return result, 1

        temporary.mkdir(parents=True, exist_ok=True)
        question_file = temporary / "logos-question.txt"
        question_file.write_text(question, encoding="utf-8")
        context = temporary / "logos-context.json"
        knowledge = temporary / "logos-knowledge.json"
        runtime = temporary / "logos-venv"
        python = runtime / "bin/python"
        commands = [
            ("environment", [sys.executable, "-m", "venv", str(runtime)]),
            (
                "dependencies",
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(root / "requirements.txt"),
                ],
            ),
            (
                "context",
                [
                    str(python),
                    "-m",
                    "scripts.logos_context",
                    "--repo-root",
                    str(root),
                    "--issue",
                    issue,
                    "--pr",
                    pr,
                    "--reference-case",
                    "--owner-context-file",
                    "docs/agents/logos-native-quality.md",
                    "--out",
                    str(context),
                ],
            ),
            (
                "knowledge",
                [
                    str(python),
                    "-m",
                    "scripts.logos_knowledge",
                    "--context-file",
                    str(context),
                    "--cache-owner",
                    environ["GITHUB_REPOSITORY_OWNER"],
                    "--cache-access-scope",
                    "repository:" + environ["GITHUB_REPOSITORY"],
                    "--out",
                    str(knowledge),
                ],
            ),
            (
                "dialogue",
                [
                    str(python),
                    "-m",
                    "scripts.logos_dialogue",
                    "--question-file",
                    str(question_file),
                    "--context-file",
                    str(context),
                    "--knowledge-file",
                    str(knowledge),
                    "--out",
                    str(answer),
                ],
            ),
        ]
        child_env = dict(environ)
        child_env["LOGOS_MEMORY_PATH"] = str(temporary / "logos-dialogue.memory")
        child_env["LOGOS_PROVIDER_CREDENTIAL_KEY"] = secrets.token_urlsafe(32)
        for phase, command in commands:
            print("logos phase=" + phase, flush=True)
            stage_env = {
                key: value
                for key, value in child_env.items()
                if not re.search(
                    r"TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY", key, re.IGNORECASE
                )
                and key != "YANDEX_PROJECT_ID"
            }
            allowed_credentials = {
                "context": ("GITHUB_TOKEN",),
                "knowledge": ("YANDEX_API_KEY", "YANDEX_PROJECT_ID"),
                "dialogue": (
                    "GITHUB_TOKEN",
                    "YANDEX_API_KEY",
                    "YANDEX_PROJECT_ID",
                    "LOGOS_PROVIDER_CREDENTIAL_KEY",
                ),
            }.get(phase, ())
            stage_env.update(
                {key: child_env[key] for key in allowed_credentials if key in child_env}
            )
            # Do not export arbitrary subprocess diagnostics, env or credential values.
            if phase == "dialogue":
                run_dialogue_command(command, root, stage_env, answer)
            else:
                subprocess.run(
                    command, cwd=root, env=stage_env, check=True, capture_output=True
                )
        candidate = json.loads(answer.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise TypeError("Native result must be an object")
        result = candidate
        return result, 0 if result.get("status") == "completed" else 1
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        subprocess.CalledProcessError,
    ) as exc:
        # A provider checkpoint can exist even when its subprocess fails. Preserve it.
        if phase == "dialogue" and answer.is_file():
            try:
                checkpoint = json.loads(answer.read_text(encoding="utf-8"))
                if isinstance(checkpoint, dict):
                    result = checkpoint
                    result["native_status"] = result.get("status")
            except (OSError, ValueError):
                pass
        result.update(
            status="failed", runner_error=type(exc).__name__, runner_phase=phase
        )
        return result, 1


def emit_result(
    result: dict[str, Any], summary: Path | None, *, marker: str = "LOGOS_RESULT"
) -> None:
    """Keep the exact JSON in reconstructable log chunks, including long Unicode replies."""
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = [encoded[i : i + 12000] for i in range(0, len(encoded), 12000)]
    for index, chunk in enumerate(chunks, 1):
        print(f"{marker}_PART={index}/{len(chunks)}:{chunk}", flush=True)
    print(marker + "_SHA256=" + hashlib.sha256(payload).hexdigest(), flush=True)
    print(
        marker
        + "_EXECUTION_STATUS="
        + json.dumps(result.get("status", "unknown"), ensure_ascii=True),
        flush=True,
    )
    if summary is not None:
        text = (
            "Execution status: "
            + html.escape(str(result.get("status", "unknown")))
            + "\n\n"
        )
        if result.get("launch_hold"):
            text += (
                "Court hold: "
                + html.escape(str(result["launch_hold"]["case"]))
                + "\n\n"
            )
        reply = result.get("reply")
        if isinstance(reply, str):
            rendered = "<pre>" + html.escape(reply) + "</pre>\n"
            if len(rendered.encode("utf-8")) <= 900000:
                text += rendered
            else:
                text += "Full reply exceeds the summary limit; exact JSON is in LOGOS_RESULT_PART log chunks.\n"
        with summary.open("a", encoding="utf-8") as output:
            output.write(text)


def main() -> int:
    # Direct script invocation must import the checked-out repository, not the scripts directory.
    sys.path.insert(0, str(ROOT))
    temporary = Path(os.environ["RUNNER_TEMP"]) / "logos-once"
    result, exit_code = run_native(ROOT, temporary, dict(os.environ))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    emit_result(result, Path(summary) if summary else None)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
