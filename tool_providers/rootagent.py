"""Logos tools for the Redmi 9 RootAgent, run as a GitHub Actions job.

Logos drives the owner's phone as a user: status, probe, capture, tap, swipe,
text, key, browse, batch, and the legacy relative move/click.

When Logos runs on the phone itself (the normal deployment), ``rootagent_run``
executes the device CLI directly through the root worker and returns the result
synchronously — no GitHub, no token. The CLI's ``device_guard`` pins the Redmi 9
identity as root on every command, which is the whole trust boundary.

Only when Logos is not on the phone does ``rootagent_run`` fall back to
dispatching the ``rootagent_command`` action of the Redmi 9 app server workflow on
``develop`` and returning a run id for ``rootagent_result`` to read. That path
needs ``LOGOS_ROOTAGENT_DISPATCH_TOKEN`` and ``LOGOS_ROOTAGENT_REPOSITORY`` in the
environment; the token is never returned, logged or written into an error text.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from tool_providers.schema import object_schema

TOKEN_ENV = "LOGOS_ROOTAGENT_DISPATCH_TOKEN"
REPOSITORY_ENV = "LOGOS_ROOTAGENT_REPOSITORY"
API = "https://api.github.com"
API_TIMEOUT_SECONDS = 30
WORKFLOW_FILE = "redmi9-app-server.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW_FILE
ACTION = "rootagent_command"
BRANCH = "develop"
COMMANDS = (
    "status",
    "probe",
    "ui",
    "capture",
    "move",
    "click",
    "tap",
    "swipe",
    "text",
    "key",
    "browse",
    "batch",
)
BATCH_LIMIT = 30
URL_PATTERN = r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?#][^\s]*)?"
URL = re.compile(URL_PATTERN)
URL_LIMIT = 2048
DELTA_LIMIT = 500
COORD_LIMIT = 99999
DURATION_LIMIT = 60000
TEXT_LIMIT = 4096
# The phone types through a virtual US keyboard: printable ASCII and tab, nothing else
# (scripts/redmi_root_agent.py ASCII_KEYS). Cyrillic and emoji cannot be typed.
KEYBOARD_CHARS = frozenset(chr(code) for code in range(32, 127)) | {"\t"}
KEYBOARD_TEXT_ERROR = (
    "text набирается виртуальной клавиатурой US: только латиница, цифры, пробел, "
    "табуляция и ASCII-знаки; кириллица и эмодзи не набираются"
)
PACKAGE_PATTERN = r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+"
PACKAGE = re.compile(PACKAGE_PATTERN)
KEYCODE_PATTERN = r"KEYCODE_[A-Z0-9_]+|[0-9]{1,4}"
KEYCODE = re.compile(KEYCODE_PATTERN)
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# The phone runner cannot run Node20 JS actions, so the job publishes its result
# and any PNG to this branch through the GitHub API instead of as artifacts.
RESULTS_BRANCH = "rootagent-results"


class _Failure(Exception):
    """A failed call, reported to the caller as the failed tool result it carries."""

    def __init__(self, message: str, phase: str, **metadata: Any) -> None:
        super().__init__(message)
        self.result = {
            "success": False,
            "error": message,
            "metadata": {"phase": phase, **metadata},
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are returned, not followed, so the token never reaches another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _bounded_int(args: Mapping[str, Any], key: str, low: int, high: int) -> int:
    value = args.get(key)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{key} должен быть целым числом от {low} до {high}")
    return value


def _coord(args: Mapping[str, Any], key: str) -> int:
    return _bounded_int(args, key, 0, COORD_LIMIT)


def _validated(args: Mapping[str, Any]) -> tuple[str, str]:
    """The command and the workflow's rootagent_args text, under the job's own rules.

    The device drives the whole phone as a user; the coordinates and deltas are
    re-checked against the real screen on the device. No command is gated here.
    """
    command = args.get("command")
    if command not in COMMANDS:
        raise ValueError("command должна быть одной из: " + ", ".join(COMMANDS))
    package = args.get("package")
    if command in ("status", "probe", "ui"):
        return command, ""
    if command == "batch":
        steps = args.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= BATCH_LIMIT or any(
            not isinstance(step, dict) or len(step) != 1 for step in steps
        ):
            raise ValueError(
                "batch требует steps: список из 1..30 шагов вида "
                '{"tap": [x, y]}, {"text": "..."}, {"key": "KEYCODE_ENTER"}, '
                '{"swipe": [x, y, x2, y2]}, {"wait": мс}, {"browse": "https://..."}'
            )
        if any(
            isinstance(step.get("text"), str)
            and any(ch not in KEYBOARD_CHARS for ch in step["text"])
            for step in steps
        ):
            raise ValueError(KEYBOARD_TEXT_ERROR)
        return command, json.dumps(steps, ensure_ascii=False)
    if command == "browse":
        url = args.get("url")
        if not isinstance(url, str) or len(url) > URL_LIMIT or URL.fullmatch(url) is None:
            raise ValueError("browse требует url вида https://ru.wikipedia.org")
        return command, url
    if command == "capture":
        if package is None:
            return command, ""
        if not isinstance(package, str) or PACKAGE.fullmatch(package) is None:
            raise ValueError("capture принимает необязательный package вида com.example.app")
        return command, package
    if command in ("move", "click"):
        dx = _bounded_int(args, "dx", -DELTA_LIMIT, DELTA_LIMIT)
        dy = _bounded_int(args, "dy", -DELTA_LIMIT, DELTA_LIMIT)
        if package is None:
            return command, f"{dx} {dy}"
        if not isinstance(package, str) or PACKAGE.fullmatch(package) is None:
            raise ValueError("package должен быть вида com.example.app")
        return command, f"{dx} {dy} {package}"
    if command == "tap":
        return command, f"{_coord(args, 'x')} {_coord(args, 'y')}"
    if command == "swipe":
        x, y, x2, y2 = (_coord(args, key) for key in ("x", "y", "x2", "y2"))
        duration = args.get("duration_ms")
        if duration is None:
            return command, f"{x} {y} {x2} {y2}"
        duration = _bounded_int(args, "duration_ms", 1, DURATION_LIMIT)
        return command, f"{x} {y} {x2} {y2} {duration}"
    if command == "text":
        text = args.get("text")
        if not isinstance(text, str) or not text or len(text) > TEXT_LIMIT:
            raise ValueError(f"text должен быть непустой строкой не длиннее {TEXT_LIMIT} символов")
        if any(ch in text for ch in ("\x00", "\n", "\r")):
            raise ValueError("text должен быть одной строкой без переводов строки")
        if any(ch not in KEYBOARD_CHARS for ch in text):
            raise ValueError(KEYBOARD_TEXT_ERROR)
        return command, text
    keycode = args.get("keycode")
    if isinstance(keycode, int) and 0 <= keycode <= 9999:
        keycode = str(keycode)
    if not isinstance(keycode, str) or KEYCODE.fullmatch(keycode) is None:
        raise ValueError("keycode должен быть вида KEYCODE_HOME или числом, например 3")
    return command, keycode


def _configured() -> tuple[str, str, str]:
    """The token and the owner and name of the repository, or the failure that names what is missing."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise _Failure(
            f"Не задана переменная {TOKEN_ENV} на сервере Logos; GitHub не вызван",
            "authorization",
        )
    repository = os.environ.get(REPOSITORY_ENV, "").strip()
    if not repository:
        raise _Failure(
            f"Не задана переменная {REPOSITORY_ENV} (владелец/репозиторий); GitHub не вызван",
            "execution",
        )
    if REPOSITORY.fullmatch(repository) is None:
        raise _Failure(f"{REPOSITORY_ENV} должна быть в виде владелец/репозиторий", "execution")
    owner, name = repository.split("/", 1)
    return token, owner, name


def _repository_url(owner: str, name: str, path: str) -> str:
    return (
        f"{API}/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/{path}"
    )


def _github(
    method: str, url: str, token: str, body: Mapping[str, Any] | None = None
) -> tuple[int, bytes]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "logos-rootagent",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with _OPENER.open(request, timeout=API_TIMEOUT_SECONDS) as response:
        return response.status, response.read()


def _github_message(error: urllib.error.HTTPError, token: str) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8", "replace"))
        message = payload.get("message") if isinstance(payload, dict) else None
    except (OSError, ValueError, AttributeError):
        message = None
    text = str(message or error.reason or "запрос отклонён")
    return text.replace(token, "[скрыто]")[:300]


def _http_failure(error: urllib.error.HTTPError, token: str, **metadata: Any) -> _Failure:
    return _Failure(
        f"GitHub отклонил запрос (HTTP {error.code}): {_github_message(error, token)}",
        "execution",
        http_status=error.code,
        **metadata,
    )


def _api_json(owner: str, name: str, token: str, path: str) -> Any:
    try:
        _, raw = _github("GET", _repository_url(owner, name, path), token)
    except urllib.error.HTTPError as error:
        raise _http_failure(error, token) from None
    except Exception as error:
        raise _Failure(
            f"GitHub не ответил на чтение запуска ({type(error).__name__}); повторите позже",
            "execution",
        ) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise _Failure("GitHub вернул не JSON", "execution") from None


def _dispatched_run(raw: bytes) -> tuple[int | None, str | None]:
    """The run id and page URL of a dispatch that returned them; GitHub may answer 204 without them."""
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        return None, None
    if not isinstance(payload, dict) or type(payload.get("workflow_run_id")) is not int:
        return None, None
    html = payload.get("html_url")
    return payload["workflow_run_id"], html if isinstance(html, str) else None


def _put_pending(
    owner: str, name: str, token: str, run_id: int, command: str, args: str
) -> None:
    """Write this run's request to pending/<run_id>.json on the results branch."""

    def send(method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        try:
            _, raw = _github(method, _repository_url(owner, name, path), token, body)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise _http_failure(error, token) from None
        except Exception as error:
            raise _Failure(
                f"GitHub не ответил при записи запроса ({type(error).__name__})",
                "execution",
            ) from None
        return json.loads(raw) if raw else {}

    if send("GET", f"git/ref/heads/{RESULTS_BRANCH}") is None:
        base = send("GET", f"git/ref/heads/{BRANCH}")
        if not isinstance(base, dict):
            raise _Failure(f"Не найдена ветка {BRANCH}", "execution")
        send(
            "POST",
            "git/refs",
            {"ref": f"refs/heads/{RESULTS_BRANCH}", "sha": base["object"]["sha"]},
        )
    content = json.dumps({"command": command, "args": args}).encode("utf-8")
    send(
        "PUT",
        f"contents/pending/{run_id}.json",
        {
            "message": f"rootagent request {run_id}",
            "content": base64.b64encode(content).decode("ascii"),
            "branch": RESULTS_BRANCH,
        },
    )


def _results_file(
    owner: str, name: str, token: str, path: str
) -> tuple[bool, bytes | None]:
    """Read one file from the rootagent-results branch via the contents API.

    Returns (found, bytes). A 404 means the job has not published it yet.
    """
    quoted = urllib.parse.quote(path)
    url = _repository_url(owner, name, f"contents/{quoted}?ref={RESULTS_BRANCH}")
    try:
        _, raw = _github("GET", url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False, None
        raise _http_failure(error, token) from None
    except Exception as error:
        raise _Failure(
            f"GitHub не ответил на чтение результата ({type(error).__name__})", "execution"
        ) from None
    try:
        payload = json.loads(raw)
        if payload.get("encoding") != "base64":
            raise ValueError
        return True, base64.b64decode(payload["content"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise _Failure("GitHub вернул результат в неверном виде", "execution") from None


# The device CLI, relative to a repository root; the launcher exports these roots.
CLI_RELATIVE = ("scripts", "redmi_root_agent.py")
ROOT_ENV = ("LOGOS_PROJECT_ROOT", "LOGOS_LOCAL_REPO_DIR", "LOGOS_APP_DIR")
LOCAL_TIMEOUT_SECONDS = 330
# Commands whose worker result carries a screenshot as png_base64.
CAPTURE_COMMANDS = ("capture", "browse", "batch")


def _local_cli() -> str | None:
    """The on-device CLI path when Logos runs on the phone itself, else None.

    getprop marks an Android device; the CLI's own device_guard then pins the
    Redmi 9 as root on every command, so no GitHub runner or token is involved.
    """
    if not os.path.exists("/system/bin/getprop"):
        return None
    roots = [os.environ.get(name) for name in ROOT_ENV]
    roots.append(str(Path(__file__).resolve().parent.parent))
    for root in roots:
        if root:
            cli = Path(root, *CLI_RELATIVE)
            if cli.is_file():
                return str(cli)
    return None


def _worker_request(command: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """The structured worker request for the local CLI, after _validated accepted args."""
    request: dict[str, Any] = {"command": command}
    package = args.get("package")
    if command in ("capture", "move", "click") and isinstance(package, str) and package:
        request["expected_package"] = package
    if command in ("move", "click"):
        request["dx"], request["dy"] = args["dx"], args["dy"]
    elif command == "tap":
        request["x"], request["y"] = args["x"], args["y"]
    elif command == "swipe":
        for key in ("x", "y", "x2", "y2"):
            request[key] = args[key]
        if args.get("duration_ms") is not None:
            request["duration_ms"] = args["duration_ms"]
    elif command == "text":
        request["text"] = args["text"]
    elif command == "key":
        keycode = args.get("keycode")
        request["keycode"] = str(keycode) if isinstance(keycode, int) else keycode
    elif command == "browse":
        request["url"] = args["url"]
    elif command == "batch":
        request["steps"] = args["steps"]
    return request


def _run_local(cli: str, command: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Run one command on the phone directly through the root worker, synchronously."""
    request = _worker_request(command, args)
    workdir = tempfile.mkdtemp(prefix="rootagent-")
    request_path = os.path.join(workdir, "request.json")
    try:
        with open(request_path, "w", encoding="utf-8") as handle:
            json.dump(request, handle, ensure_ascii=False)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", cli, "--local-request", request_path],
                capture_output=True,
                timeout=LOCAL_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _Failure(
                "Команда на телефоне не завершилась за отведённое время", "execution",
                local=True, command=command,
            ).result
        except OSError as error:
            return _Failure(
                f"Не удалось запустить устройство локально ({type(error).__name__})",
                "execution", local=True, command=command,
            ).result
    finally:
        try:
            os.remove(request_path)
        except OSError:
            pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass
    line = completed.stdout.splitlines()[-1] if completed.stdout.strip() else b""
    try:
        payload = json.loads(line)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return _Failure(
            "Устройство вернуло не JSON-объект", "execution", local=True, command=command
        ).result
    if completed.returncode or payload.get("ok") is False:
        return _Failure(
            f"Устройство отклонило команду: {payload.get('error', 'root_worker_failed')}",
            "execution", local=True, command=command,
        ).result
    # run_client prints the worker metadata directly; a screenshot rides as png_base64.
    result = payload
    capture = None
    encoded = result.pop("png_base64", None)
    if isinstance(encoded, str) and encoded:
        try:
            png = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            png = b""
        if png:
            capture = {
                "bytes": len(png),
                "sha256": hashlib.sha256(png).hexdigest(),
                "png_base64": encoded,
            }
    return {
        "success": True,
        "local": True,
        "command": command,
        "submitted": True,
        "verified": True,
        "result": result,
        "capture": capture,
        "note": "Команда выполнена на телефоне напрямую; результат уже здесь.",
    }


def rootagent_run(
    arguments: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    command, argument_text = _validated(dict(arguments or {}))
    cli = _local_cli()
    if cli is not None:
        return _run_local(cli, command, dict(arguments or {}))
    try:
        token, owner, name = _configured()
        body = {
            "ref": BRANCH,
            "inputs": {
                "action": ACTION,
                "rootagent_command": command,
                "rootagent_args": argument_text,
            },
            "return_run_details": True,
        }
        try:
            _, raw = _github(
                "POST",
                _repository_url(owner, name, f"actions/workflows/{WORKFLOW_FILE}/dispatches"),
                token,
                body,
            )
        except urllib.error.HTTPError as error:
            raise _http_failure(error, token, dispatch="not_sent") from None
        except Exception as error:
            raise _Failure(
                "Исход запуска неизвестен: ответ GitHub не получен "
                f"({type(error).__name__}). Не отправляйте запуск повторно: "
                "сначала проверьте запуски в Actions",
                "execution",
                dispatch="unknown",
            ) from None
    except _Failure as failure:
        return failure.result
    run_id, run_url = _dispatched_run(raw)
    if run_id is None:
        return {
            "success": False,
            "error": (
                "GitHub принял запуск, но не вернул его номер. Не отправляйте запуск "
                "повторно: найдите его в Actions на ветке develop"
            ),
            "metadata": {"phase": "execution", "dispatch": "accepted", "command": command},
        }
    # The phone runner's job reads its request from pending/<run_id>.json on the
    # results branch (it cannot resolve workflow inputs), waiting up to a minute.
    try:
        _put_pending(owner, name, token, run_id, command, argument_text)
    except _Failure as failure:
        failure.result["error"] = (
            f"Запуск {run_id} поставлен, но запрос не доставлен на ветку "
            f"{RESULTS_BRANCH}: {failure.result['error']}"
        )
        failure.result["metadata"].update({"run_id": run_id, "dispatch": "accepted"})
        return failure.result
    return {
        "success": True,
        "run_id": run_id,
        "run_url": run_url,
        "command": command,
        "branch": BRANCH,
        "submitted": True,
        "verified": False,
        "note": (
            "Запуск поставлен в очередь на ветке develop. Результат читается через "
            "rootagent_result по run_id."
        ),
    }


def rootagent_result(
    arguments: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    run_id = dict(arguments or {}).get("run_id")
    if type(run_id) is not int or run_id < 1:
        raise ValueError("run_id должен быть положительным целым числом из rootagent_run")
    try:
        token, owner, name = _configured()
        run = _api_json(owner, name, token, f"actions/runs/{run_id}")
        if not isinstance(run, dict) or run.get("path") != WORKFLOW_PATH:
            raise _Failure(
                f"Запуск {run_id} не является запуском rootagent_command", "validation"
            )
        result: dict[str, Any] = {
            "success": True,
            "run_id": run_id,
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "run_url": run.get("html_url"),
            "result": None,
            "capture": None,
        }
        if run.get("status") != "completed":
            return result
        found, raw = _results_file(owner, name, token, f"runs/{run_id}.json")
        if not found or raw is None:
            result["note"] = (
                "Результат ещё не опубликован на ветке rootagent-results; "
                "повторите чтение немного позже"
            )
            return result
        try:
            result["result"] = json.loads(raw)
        except ValueError:
            raise _Failure("Опубликованный результат не JSON", "execution") from None
        png_found, png = _results_file(owner, name, token, f"runs/{run_id}.png")
        if png_found and png is not None:
            result["capture"] = {
                "branch": RESULTS_BRANCH,
                "path": f"runs/{run_id}.png",
                "bytes": len(png),
                "sha256": hashlib.sha256(png).hexdigest(),
                "png_base64": base64.b64encode(png).decode("ascii"),
            }
        return result
    except _Failure as failure:
        return failure.result


def _optional(schema: Mapping[str, Any]) -> dict[str, Any]:
    """An argument that may be absent or null; the executor validates the schema as written."""
    return {"anyOf": [dict(schema), {"type": "null"}]}


_RUN_PROPERTIES = {
    "command": {
        "type": "string",
        "enum": list(COMMANDS),
        "description": (
            "status и probe читают состояние; ui читает экран текстом — все элементы с "
            "текстом, описанием, id и center для tap (дёшево, без снимка); capture снимает "
            "экран и тоже возвращает элементы; tap нажимает в точке "
            "(x, y); swipe проводит от (x, y) к (x2, y2); text вводит строку латиницей "
            "(ASCII); key шлёт клавишу (keycode); browse открывает url и снимает экран; "
            "batch выполняет steps подряд и снимает экран; move и click — относительное "
            "движение мыши (dx, dy)"
        ),
    },
    "x": _optional(
        {
            "type": "integer",
            "minimum": 0,
            "maximum": COORD_LIMIT,
            "description": "Абсолютная координата X экрана для tap и swipe",
        }
    ),
    "y": _optional(
        {
            "type": "integer",
            "minimum": 0,
            "maximum": COORD_LIMIT,
            "description": "Абсолютная координата Y экрана для tap и swipe",
        }
    ),
    "x2": _optional(
        {
            "type": "integer",
            "minimum": 0,
            "maximum": COORD_LIMIT,
            "description": "Конечная координата X для swipe",
        }
    ),
    "y2": _optional(
        {
            "type": "integer",
            "minimum": 0,
            "maximum": COORD_LIMIT,
            "description": "Конечная координата Y для swipe",
        }
    ),
    "duration_ms": _optional(
        {
            "type": "integer",
            "minimum": 1,
            "maximum": DURATION_LIMIT,
            "description": "Длительность swipe в миллисекундах; необязательно",
        }
    ),
    "text": _optional(
        {
            "type": "string",
            "minLength": 1,
            "maxLength": TEXT_LIMIT,
            "pattern": "^[\\t -~]+$",
            "description": (
                "Строка для ввода командой text: одна строка, только латиница, цифры, "
                "пробел и ASCII-знаки (виртуальная клавиатура US, кириллица не набирается)"
            ),
        }
    ),
    "steps": _optional(
        {
            "type": "array",
            "minItems": 1,
            "maxItems": BATCH_LIMIT,
            "items": {"type": "object", "minProperties": 1, "maxProperties": 1},
            "description": (
                "Шаги batch, выполняются подряд за один вызов, в конце снимок экрана: "
                '{"tap": [x, y]}, {"swipe": [x, y, x2, y2, мс?]}, {"text": "ASCII"}, '
                '{"key": "KEYCODE_ENTER"}, {"wait": мс}, {"browse": "https://..."}'
            ),
        }
    ),
    "url": _optional(
        {
            "type": "string",
            "maxLength": URL_LIMIT,
            "pattern": "^" + URL_PATTERN + "$",
            "description": "Адрес для browse: разбудить экран, открыть его в браузере и снять экран",
        }
    ),
    "keycode": _optional(
        {
            "type": "string",
            "pattern": "^(?:" + KEYCODE_PATTERN + ")$",
            "description": "Клавиша для key: имя вида KEYCODE_HOME, KEYCODE_BACK или число",
        }
    ),
    "dx": _optional(
        {
            "type": "integer",
            "minimum": -DELTA_LIMIT,
            "maximum": DELTA_LIMIT,
            "description": "Относительное смещение по X для move и click, от -500 до 500; не координата экрана",
        }
    ),
    "dy": _optional(
        {
            "type": "integer",
            "minimum": -DELTA_LIMIT,
            "maximum": DELTA_LIMIT,
            "description": "Относительное смещение по Y для move и click, от -500 до 500; не координата экрана",
        }
    ),
    "package": _optional(
        {
            "type": "string",
            "pattern": "^" + PACKAGE_PATTERN + "$",
            "description": "Необязательное имя пакета Android на переднем плане, например com.example.app",
        }
    ),
}

_TRANSPORTS = ["responses_api", "local_agent", "mcp"]

ROOTAGENT_TOOLS: dict[str, dict[str, Any]] = {
    "rootagent_run": {
        "title": "Redmi 9 RootAgent: запуск команды",
        "description": (
            "Управлять телефоном Redmi 9 как пользователь: status, probe, ui (экран текстом: "
            "элементы ui_elements с text, description, id, flags и center — нажимай tap в "
            "center нужного элемента), capture (снимок экрана + ui_elements), tap (нажатие в точке), swipe (свайп), text (ввод строки), key (клавиша, "
            "в т.ч. системная), browse (разбудить экран, открыть url в браузере и сразу снять "
            "экран), move и click (относительная мышь). Когда Логос работает на самом телефоне, "
            "команда выполняется на устройстве напрямую и результат возвращается сразу в поле "
            "result/capture — rootagent_result не нужен; снимок экрана (capture/browse/batch) "
            "модель с поддержкой изображений получает картинкой в следующем запросе. Если Логос не на телефоне, команда ставится как задание GitHub Actions на "
            "develop: тогда возвращается run_id, а результат читается через rootagent_result."
        ),
        "parameters": object_schema(_RUN_PROPERTIES, ["command"]),
        "capabilities": ["device", "rootagent", "github_actions"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": False,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "func": rootagent_run,
    },
    "rootagent_result": {
        "title": "Redmi 9 RootAgent: результат запуска",
        "description": (
            "Прочитать запуск rootagent_run по run_id: статус, итог и JSON-результат команды, "
            "когда запуск завершён. Результат и снимок читаются с ветки rootagent-results, "
            "куда задание публикует их через GitHub API; снимок модель с поддержкой "
            "изображений получает картинкой в следующем запросе."
        ),
        "parameters": object_schema(
            {
                "run_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Номер запуска, который вернул rootagent_run",
                }
            },
            ["run_id"],
        ),
        "capabilities": ["device", "rootagent", "github_actions"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "func": rootagent_result,
    },
}

__all__ = ["ROOTAGENT_TOOLS"]
