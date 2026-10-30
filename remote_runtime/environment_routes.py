"""REST API for branch-aware application environments."""

from flask import Blueprint, Response, current_app, jsonify, request

from remote_runtime.environments import (
    GitUnavailable,
    _get,
    create_environment,
    delete_environment,
    dispatch_environment_http,
    list_environments,
    register_runtime_operation,
    restart_environment,
    start_environment,
    stop_environment,
)
from remote_runtime.host import get_current_owner_id

environment_bp = Blueprint("environments", __name__, url_prefix="/api/environments")
environment_gateway_bp = Blueprint("environment_gateway", __name__)

# What the person sees for each error: what happened and what to do. The machine
# ``error`` code and the HTTP status stay as they were.
_DETAILS = {
    "branch is required": "Укажите ветку и повторите.",
    "environment_ref_invalid": (
        "Окружение не создано: ветка или коммит не найдены. "
        "Проверьте их имена и повторите."
    ),
    "git is unavailable on this server": (
        "На сервере Logos нет git, поэтому окружение не создать. "
        "Установите git на сервере Logos."
    ),
    "environment_not_found": (
        "Окружение не найдено: его удалили. Обновите список окружений."
    ),
    "environment_state_conflict": (
        "Сейчас это действие недоступно для окружения. "
        "Обновите список и проверьте статус окружения."
    ),
    "environment_operation_failed": (
        "Операция с окружением не выполнена. Проверьте статус окружения и повторите."
    ),
    "environment_not_running": "Окружение не запущено. Запустите его и откройте снова.",
    "environment_runtime_timeout": "Окружение не ответило вовремя. Повторите запрос.",
    "environment_runtime_unreachable": (
        "Окружение не отвечает. Перезапустите его и откройте снова."
    ),
}


def _error(error, detail_of=None):
    """An error body: the machine ``error`` as given and its Russian ``detail``."""
    return {"error": error, "detail": _DETAILS[detail_of or error]}


@environment_bp.get("")
def environments_list():
    return jsonify({"environments": list_environments()})


@environment_bp.post("")
def environments_create():
    data = request.get_json(silent=True) or {}
    branch = str(data.get("branch") or "").strip()
    commit_sha = data.get("commit_sha")
    if not branch:
        return jsonify(_error("branch is required")), 400
    try:
        return jsonify(
            create_environment(branch, commit_sha, get_current_owner_id())
        ), 201
    except ValueError as exc:
        return jsonify(_error(str(exc), "environment_ref_invalid")), 400
    except GitUnavailable:
        return jsonify(_error("git is unavailable on this server")), 503


@environment_bp.get("/<environment_id>")
def environments_get(environment_id):
    item = _get(environment_id)
    if not item:
        return jsonify(_error("environment_not_found")), 404
    return jsonify(item)


@environment_bp.post("/<environment_id>/start")
def environments_start(environment_id):
    try:
        return jsonify(start_environment(environment_id))
    except KeyError:
        return jsonify(_error("environment_not_found")), 404
    except ValueError as exc:
        return jsonify(_error(str(exc), "environment_state_conflict")), 409
    except Exception:
        return jsonify(_error("environment_operation_failed")), 500


@environment_bp.post("/<environment_id>/stop")
def environments_stop(environment_id):
    try:
        return jsonify(stop_environment(environment_id))
    except KeyError:
        return jsonify(_error("environment_not_found")), 404
    except ValueError as exc:
        return jsonify(_error(str(exc), "environment_state_conflict")), 409


@environment_bp.post("/<environment_id>/restart")
def environments_restart(environment_id):
    try:
        return jsonify(restart_environment(environment_id))
    except KeyError:
        return jsonify(_error("environment_not_found")), 404
    except ValueError as exc:
        return jsonify(_error(str(exc), "environment_state_conflict")), 409
    except Exception:
        return jsonify(_error("environment_operation_failed")), 500


@environment_bp.delete("/<environment_id>")
def environments_delete(environment_id):
    try:
        return jsonify(delete_environment(environment_id))
    except KeyError:
        return jsonify(_error("environment_not_found")), 404
    except ValueError as exc:
        return jsonify(_error(str(exc), "environment_state_conflict")), 409
    except Exception:
        return jsonify(_error("environment_operation_failed")), 500


def _runtime_http_request(context, payload):
    app = payload.get("_app")
    if app is None:
        raise RuntimeError("runtime application is unavailable")

    path = str(payload.get("path") or "/")
    headers = list(payload.get("headers") or [])
    client = app.test_client()
    response = client.open(
        path,
        method=str(payload.get("method") or "GET"),
        query_string=str(payload.get("query_string") or ""),
        data=payload.get("body") or b"",
        headers=headers,
        follow_redirects=False,
        buffered=False,
    )
    excluded = {"content-length", "connection", "transfer-encoding", "content-encoding"}
    return {
        "status_code": response.status_code,
        "headers": [
            (key, value)
            for key, value in response.headers.items()
            if key.lower() not in excluded
        ],
        "body": response.iter_encoded(),
        "close": response.close,
    }


register_runtime_operation("http.request", _runtime_http_request)


def _proxy(environment_id, subpath=""):
    item = _get(environment_id)
    if not item:
        return jsonify(_error("environment_not_found")), 404
    if item.get("status") != "RUNNING" or not item.get("runtime_thread_id"):
        return jsonify(_error("environment_not_running")), 503

    # Accept-Encoding stays here: the inner app answers uncompressed and this response is compressed once, on the
    # way out (web_shell.compression); the inner body is forwarded without its Content-Encoding.
    headers = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
    ]
    try:
        upstream = dispatch_environment_http(
            environment_id,
            {
                "_app": current_app._get_current_object(),
                "method": request.method,
                "path": "/" + subpath.lstrip("/"),
                "query_string": request.query_string.decode("latin-1"),
                "body": request.get_data(),
                "headers": headers,
                "base_path": f"/environments/{environment_id}",
            },
            timeout=30,
        )
    except TimeoutError:
        return jsonify(_error("environment_runtime_timeout")), 504
    except RuntimeError:
        return jsonify(_error("environment_not_running")), 503
    except Exception:
        return jsonify(_error("environment_runtime_unreachable")), 502

    return Response(
        upstream,
        status=upstream.status_code,
        headers=upstream.headers,
        direct_passthrough=True,
    )


@environment_gateway_bp.route(
    "/environments/<environment_id>",
    defaults={"subpath": ""},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@environment_gateway_bp.route(
    "/environments/<environment_id>/",
    defaults={"subpath": ""},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@environment_gateway_bp.route(
    "/environments/<environment_id>/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def environment_gateway(environment_id, subpath):
    return _proxy(environment_id, subpath)
