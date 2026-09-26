"""REST API for branch-aware application environments."""

from flask import Blueprint, Response, jsonify, request

from environment_manager import (
    create_environment,
    delete_environment,
    list_environments,
    restart_environment,
    start_environment,
    stop_environment,
    _get,
)
from treasury_identity import TreasuryIdentityError, get_current_owner_id
from runtime_dispatcher import RuntimeDispatcher, RuntimeDispatchError

environment_bp = Blueprint("environments", __name__, url_prefix="/api/environments")
environment_gateway_bp = Blueprint("environment_gateway", __name__)
runtime_dispatcher = RuntimeDispatcher(_get)


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


def _proxy(environment_id, subpath=""):
    try:
        dispatched = runtime_dispatcher.dispatch(
            environment_id,
            owner_id=_owner(),
            method=request.method,
            subpath=subpath,
            query=request.args,
            body=request.get_data(),
            headers=request.headers,
            cookies=request.cookies,
        )
    except RuntimeDispatchError as exc:
        return jsonify({"error": exc.code}), exc.status
    return Response(dispatched.body, status=dispatched.status, headers=dispatched.headers)


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
