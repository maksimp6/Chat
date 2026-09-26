"""REST API for branch-aware application environments."""

from flask import Blueprint, Response, current_app, jsonify, request

from environment_manager import (
    create_environment,
    delete_environment,
    list_environments,
    restart_environment,
    start_environment,
    stop_environment,
    _get,
    authorize_environment_runtime,
    dispatch_environment_http,
    register_runtime_operation,
)
from runtime import RuntimeNotFound, RuntimeOwnerViolation
from treasury_identity import TreasuryIdentityError, get_current_owner_id

environment_bp = Blueprint("environments", __name__, url_prefix="/api/environments")
environment_gateway_bp = Blueprint("environment_gateway", __name__)


def _owner():
    try:
        return get_current_owner_id(required=False)
    except TreasuryIdentityError:
        return None


@environment_bp.get("")
def environments_list():
    return jsonify({"environments": list_environments(_owner())})


@environment_bp.post("")
def environments_create():
    data = request.get_json(silent=True) or {}
    branch = str(data.get("branch") or "").strip()
    commit_sha = data.get("commit_sha")
    if not branch:
        return jsonify({"error": "branch is required"}), 400
    try:
        return jsonify(create_environment(branch, commit_sha, _owner())), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@environment_bp.get("/<environment_id>")
def environments_get(environment_id):
    item = _get(environment_id)
    owner = _owner()
    if not item or (owner and item.get("owner_id") not in (None, owner)):
        return jsonify({"error": "environment_not_found"}), 404
    return jsonify(item)


@environment_bp.post("/<environment_id>/start")
def environments_start(environment_id):
    try:
        return jsonify(start_environment(environment_id, _owner()))
    except KeyError:
        return jsonify({"error": "environment_not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@environment_bp.post("/<environment_id>/stop")
def environments_stop(environment_id):
    try:
        return jsonify(stop_environment(environment_id, _owner()))
    except KeyError:
        return jsonify({"error": "environment_not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409


@environment_bp.post("/<environment_id>/restart")
def environments_restart(environment_id):
    try:
        return jsonify(restart_environment(environment_id, _owner()))
    except KeyError:
        return jsonify({"error": "environment_not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@environment_bp.delete("/<environment_id>")
def environments_delete(environment_id):
    try:
        return jsonify(delete_environment(environment_id, _owner()))
    except KeyError:
        return jsonify({"error": "environment_not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
            (key, value) for key, value in response.headers.items() if key.lower() not in excluded
        ],
        "body": response.iter_encoded(),
        "close": response.close,
    }


register_runtime_operation("http.request", _runtime_http_request)


def _proxy(environment_id, subpath=""):
    try:
        authorize_environment_runtime(environment_id, _owner())
    except (RuntimeNotFound, RuntimeOwnerViolation):
        return jsonify({"error": "environment_not_found"}), 404

    headers = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    ]
    headers.append(("X-Alice-Proxy-Authenticated", "true"))
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
        return jsonify({"error": "environment_runtime_timeout"}), 504
    except RuntimeError:
        return jsonify({"error": "environment_not_running"}), 503
    except Exception:
        return jsonify({"error": "environment_runtime_unreachable"}), 502

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
