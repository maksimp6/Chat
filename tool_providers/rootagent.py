"""Logos tools for the Redmi 9 RootAgent, run as a GitHub Actions job.

``rootagent_run`` dispatches the ``rootagent_command`` action of the Redmi 9 app
server workflow on ``develop`` and returns the run id at once. ``rootagent_result``
reads that run's status and, once the run has finished, its JSON result artifact.
Logos drives the owner's phone as a user: status, probe, capture, tap, swipe,
text, key, and the legacy relative move/click. The device work happens only
inside that job, on the existing runner and only for ``develop``.

The token and the repository come from the server environment
(``LOGOS_ROOTAGENT_DISPATCH_TOKEN``, ``LOGOS_ROOTAGENT_REPOSITORY``). The token is
never returned, logged or written into an error text.
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
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
    "capture",
    "move",
    "click",
    "tap",
    "swipe",
    "text",
    "key",
)
DELTA_LIMIT = 500
COORD_LIMIT = 99999
DURATION_LIMIT = 60000
TEXT_LIMIT = 4096
PACKAGE_PATTERN = r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+"
PACKAGE = re.compile(PACKAGE_PATTERN)
KEYCODE_PATTERN = r"KEYCODE_[A-Z0-9_]+|[0-9]{1,4}"
KEYCODE = re.compile(KEYCODE_PATTERN)
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RESULT_ARTIFACT = "rootagent-command"
CAPTURE_ARTIFACT = "rootagent-capture"
RESULT_FILE = "rootagent-result.json"
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


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
    if command in ("status", "probe"):
        return command, ""
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


def _artifact_archive(url: str, token: str) -> bytes:
    """The artifact zip. GitHub redirects to a signed URL, and that hop carries no token."""
    try:
        _, data = _github("GET", url, token)
        return data
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if error.code in REDIRECT_CODES else None
        if location is None:
            raise _http_failure(error, token) from None
    except Exception as error:
        raise _Failure(
            f"Не удалось скачать артефакт результата ({type(error).__name__})", "execution"
        ) from None
    if urllib.parse.urlsplit(location).scheme != "https":
        raise _Failure("Адрес загрузки артефакта не https: артефакт не прочитан", "execution")
    try:
        request = urllib.request.Request(location, headers={"User-Agent": "logos-rootagent"})
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            return response.read()
    except Exception as error:
        raise _Failure(
            f"Не удалось скачать артефакт результата ({type(error).__name__})", "execution"
        ) from None


def _result_json(artifact: Mapping[str, Any], token: str) -> Any:
    url = artifact.get("archive_download_url")
    if not isinstance(url, str) or not url.startswith(API + "/"):
        raise _Failure("Артефакт результата без адреса загрузки", "execution")
    data = _artifact_archive(url, token)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            payload = archive.read(RESULT_FILE)
        return json.loads(payload)
    except (KeyError, ValueError, OSError, RuntimeError, zipfile.BadZipFile):
        raise _Failure(
            f"Артефакт результата не содержит читаемый {RESULT_FILE}", "execution"
        ) from None


def _latest(artifacts: list, prefix: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and str(item.get("name") or "").startswith(prefix)
    ]
    return max(matches, key=lambda item: str(item.get("created_at") or ""), default=None)


def _artifact_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "size_in_bytes": item.get("size_in_bytes"),
        "expired": bool(item.get("expired")),
    }


def rootagent_run(
    arguments: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    command, argument_text = _validated(dict(arguments or {}))
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
            "artifacts": [],
            "result": None,
        }
        if run.get("status") != "completed":
            return result
        listing = _api_json(
            owner, name, token, f"actions/runs/{run_id}/artifacts?per_page=100"
        )
        found = listing.get("artifacts") if isinstance(listing, dict) else None
        if not isinstance(found, list):
            raise _Failure("GitHub вернул список артефактов в неверном виде", "execution")
        result_artifact = _latest(found, f"{RESULT_ARTIFACT}-{run_id}-")
        capture_artifact = _latest(found, f"{CAPTURE_ARTIFACT}-{run_id}-")
        result["artifacts"] = [
            _artifact_summary(item)
            for item in (result_artifact, capture_artifact)
            if item is not None
        ]
        if result_artifact is None:
            result["note"] = "Артефакт результата не найден: команда не записала результат"
        elif result_artifact.get("expired"):
            result["note"] = "Артефакт результата истёк по сроку хранения"
        else:
            result["result"] = _result_json(result_artifact, token)
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
            "status и probe читают состояние; capture снимает экран; tap нажимает в точке "
            "(x, y); swipe проводит от (x, y) к (x2, y2); text вводит строку; key шлёт клавишу "
            "(keycode); move и click — относительное движение мыши (dx, dy)"
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
            "description": "Строка для ввода командой text (одна строка, без переводов строки)",
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
            "Управлять телефоном Redmi 9 как пользователь через GitHub Actions: status, probe, "
            "capture (снимок экрана), tap (нажатие в точке), swipe (свайп), text (ввод строки), "
            "key (клавиша, в т.ч. системная), move и click (относительная мышь). Задание идёт "
            "на ветке develop. Инструмент сразу возвращает run_id; результат читайте через "
            "rootagent_result. Успешный ответ означает, что запуск принят, а не что действие "
            "уже выполнено на телефоне."
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
            "когда запуск завершён. Снимок PNG этим инструментом не передаётся: возвращаются только "
            "имя и размер его артефакта."
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
