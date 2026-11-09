"""The ``python`` tool: one function, ``action`` picks what to do with the backend's Python.

Owner, 05.11.2026: "make python execution tool and add alongside remember tool". The code runs
in the interpreter that runs Logos (``sys.executable``), so every package the app has is
importable, in a separate process that the tool waits for. Like run_command, no application
limit: without ``timeout_seconds`` (or with 0) the code runs until it ends (AGENTS.md).

Every run is remembered: the code and its output go into the vector database (collection
``python``) in a thread of their own, so the remember tool finds them by meaning.
"""

from __future__ import annotations

import logging
import math
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

from tool_providers._vendor.output import limit_output
from tool_providers.host import vector_add
from tool_providers.runtime_tools import _MAX_OUTPUT_BYTES, run_bounded

__all__ = ["PYTHON_TOOLS", "python"]

logger = logging.getLogger("logos.tool_providers.python_exec")

# Only what Python needs. The backend's own environment holds provider and deployment secrets.
_ENV_NAMES = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "PREFIX",
    "LD_LIBRARY_PATH",
)

# The collection the runs are embedded into.
COLLECTION = "python"


def _environment() -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_NAMES if name in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _context(cfg: dict | None) -> dict:
    context = cfg.get("_universal_context") if isinstance(cfg, dict) else None
    return context if isinstance(context, dict) else {}


def _metadata(cfg: dict | None) -> dict:
    call = _context(cfg).get("call")
    metadata = getattr(call, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _trace_event(cfg: dict | None, event_type: str, payload: dict) -> None:
    trace = _metadata(cfg).get("execution_trace")
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event(event_type, payload)


def _timeout(args: dict) -> float | None:
    raw = args.get("timeout_seconds")
    if raw in (None, 0, ""):
        return None
    timeout = float(raw)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(
            "timeout_seconds must be a positive number, 0 or null (no limit)"
        )
    return timeout


def _cwd(args: dict) -> str:
    raw = args.get("cwd")
    cwd = os.path.abspath(os.path.expanduser(str(raw))) if raw else os.getcwd()
    if not os.path.isdir(cwd):
        raise ValueError(f"Working directory does not exist: {cwd}")
    return cwd


def _run_process(
    argv: list[str], *, cwd: str, timeout: float | None, stdin: str | None
) -> dict[str, Any]:
    """Run ``argv`` and give its output, exit code and duration; the process leads its own session."""
    started = time.perf_counter()
    try:
        completed = run_bounded(
            argv, cwd=cwd, env=_environment(), timeout=timeout, stdin=stdin
        )
        return {
            "success": completed.returncode == 0,
            "stdout": limit_output(completed.stdout, _MAX_OUTPUT_BYTES),
            "stderr": limit_output(completed.stderr, _MAX_OUTPUT_BYTES),
            "exit_code": completed.returncode,
            "seconds": round(time.perf_counter() - started, 3),
            **(
                {}
                if completed.returncode == 0
                else {"error": f"Python exited with code {completed.returncode}"}
            ),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "error": f"Python timed out after {timeout:g}s",
            "stdout": limit_output(exc.stdout, _MAX_OUTPUT_BYTES),
            "stderr": limit_output(exc.stderr, _MAX_OUTPUT_BYTES),
            "exit_code": None,
            "seconds": round(time.perf_counter() - started, 3),
        }
    except OSError as exc:
        return {
            "success": False,
            "error": f"Unable to start Python: {exc}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "seconds": round(time.perf_counter() - started, 3),
        }


def _remember(
    action: str, args: dict, result: dict[str, Any], cfg: dict | None
) -> None:
    """The run goes into the vector database in its own thread (AGENTS.md: every datum is embedded)."""
    code = str(args.get("code") or "")
    text = (
        f"Инструмент python, действие {action}:\n{code}\n\n"
        + (f"stdout:\n{result.get('stdout')}\n\n" if result.get("stdout") else "")
        + (f"stderr:\n{result.get('stderr')}\n\n" if result.get("stderr") else "")
        + (f"Ошибка: {result['error']}\n" if result.get("error") else "")
        + f"Код выхода: {result.get('exit_code')}"
    )
    metadata = _metadata(cfg)
    doc_id = f"python-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    document = {
        "action": action,
        "exit_code": result.get("exit_code"),
        "success": result.get("success"),
        "conversation_id": metadata.get("conversation_id"),
    }

    def embed() -> None:
        try:
            vector_add(COLLECTION, text, doc_id, document)
        except Exception:
            logger.exception("[VECTORS] The python run %s was not embedded", doc_id)

    threading.Thread(target=embed, name=f"logos-embed-{doc_id}", daemon=True).start()
    result["remembered"] = {"collection": COLLECTION, "id": doc_id}


def _run(args: dict, cfg: dict | None) -> dict[str, Any]:
    """The code as a script of the app's interpreter; the script file lives in TMPDIR while it runs."""
    code = str(args.get("code") or "")
    if not code.strip():
        raise ValueError("code is required")
    cwd = _cwd(args)
    timeout = _timeout(args)
    stdin = args.get("stdin")
    stdin = None if stdin is None else str(stdin)
    argv_extra = args.get("argv")
    if argv_extra is not None and not (
        isinstance(argv_extra, list) and all(isinstance(a, str) for a in argv_extra)
    ):
        raise ValueError("argv must be a list of strings")
    _trace_event(
        cfg, "runtime_started", {"runtime": "python", "operation": "run", "cwd": cwd}
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="logos_python_", suffix=".py", delete=False
    ) as script:
        script.write(code)
        path = script.name
    try:
        result = _run_process(
            [sys.executable, path, *(argv_extra or [])],
            cwd=cwd,
            timeout=timeout,
            stdin=stdin,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    # The traceback names the temporary file; the model knows the code as <code>.
    for key in ("stdout", "stderr"):
        if isinstance(result.get(key), str):
            result[key] = result[key].replace(path, "<code>")
    result.update({"cwd": cwd, "timeout_seconds": timeout})
    _trace_event(
        cfg,
        "runtime_finished" if result["success"] else "runtime_failed",
        {
            "runtime": "python",
            "operation": "run",
            "cwd": cwd,
            "exit_code": result["exit_code"],
            **({"error": result["error"]} if result.get("error") else {}),
        },
    )
    return result


def _eval(args: dict, cfg: dict | None) -> dict[str, Any]:
    """One expression: its repr is the answer (``value``), printed output stays in stdout."""
    expression = str(args.get("code") or "").strip()
    if not expression:
        raise ValueError("code (the expression) is required")
    marker = f"__logos_value_{uuid.uuid4().hex}__"
    wrapped = (
        "import sys\n"
        f"__logos_result = eval(compile({expression!r}, '<code>', 'eval'))\n"
        f"sys.stdout.flush()\n"
        f"sys.stderr.write({marker!r} + repr(__logos_result) + {marker!r})\n"
    )
    result = _run({**args, "code": wrapped}, cfg)
    stderr = result.get("stderr") or ""
    if marker in stderr:
        head, _, rest = stderr.partition(marker)
        value, _, tail = rest.partition(marker)
        result["value"] = value
        result["stderr"] = head + tail
    result["action"] = "eval"
    return result


def _info(args: dict, cfg: dict | None) -> dict[str, Any]:
    """The interpreter the code runs in."""
    return {
        "success": True,
        "executable": sys.executable,
        "version": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "path": list(sys.path),
    }


_ACTIONS: dict[str, dict[str, Any]] = {
    "run": {
        "fn": _run,
        "remember": True,
        "about": "выполнить code как скрипт интерпретатора Logos (stdin — ввод, argv — аргументы, "
        "cwd — каталог, timeout_seconds — свой предел, 0 или пусто — без предела); "
        "вернёт stdout, stderr, exit_code",
    },
    "eval": {
        "fn": _eval,
        "remember": True,
        "about": "вычислить одно выражение code и вернуть его repr в value",
    },
    "info": {
        "fn": _info,
        "remember": False,
        "about": "интерпретатор: путь, версия, платформа, sys.path",
    },
}


def python(args, cfg=None):
    """One function for Python: ``action`` picks the action."""
    action = args.get("action") if isinstance(args, dict) else None
    entry = _ACTIONS.get(action) if isinstance(action, str) else None
    if entry is None:
        return {
            "success": False,
            "error": "action must be one of: " + ", ".join(_ACTIONS),
        }
    try:
        result = entry["fn"](args, cfg)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if entry["remember"]:
        _remember(action, args, result, cfg)
    return result


PYTHON_TOOLS = {
    "python": {
        "title": "Python",
        "description": "Python интерпретатора Logos (с его пакетами) одной функцией: поле action выбирает "
        "действие. "
        + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items()),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "code": {"type": "string", "minLength": 1},
                "stdin": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string", "minLength": 1, "maxLength": 4096},
                "timeout_seconds": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "capabilities": ["python", "runtime", "local_execution"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": python,
        "metadata": {"approval_actions": ["run", "eval"]},
    }
}
