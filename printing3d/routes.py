"""HTTP API for the Alice Pro 3D-printing business MVP."""

from flask import Blueprint, jsonify, request

from treasury_identity import TreasuryIdentityError, get_current_owner_id

from .service import (
    calculate_quote,
    create_order,
    get_financial_summary,
    get_order,
    list_orders,
    settle_order,
    update_order_status,
)

printing3d_bp = Blueprint("printing3d", __name__, url_prefix="/api/3d")


def _owner_id():
    try:
        return get_current_owner_id()
    except TreasuryIdentityError:
        return None


def _auth_error():
    return jsonify({"error": "authenticated owner identity is required"}), 401


@printing3d_bp.route("/quote", methods=["POST"])
def quote():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        return jsonify(calculate_quote(payload))
    except ValueError:
        return jsonify({"error": "invalid quote parameters"}), 400


@printing3d_bp.route("/orders", methods=["GET"])
def orders_list():
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    try:
        return jsonify({"orders": list_orders(owner, status=request.args.get("status"))})
    except ValueError:
        return jsonify({"error": "invalid order status"}), 400


@printing3d_bp.route("/orders", methods=["POST"])
def orders_create():
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        return jsonify(create_order(owner, payload)), 201
    except ValueError:
        return jsonify({"error": "invalid order"}), 400


@printing3d_bp.route("/orders/<order_id>", methods=["GET"])
def order_get(order_id):
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    order = get_order(owner, order_id)
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/orders/<order_id>/status", methods=["PATCH"])
def order_status(order_id):
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        order = update_order_status(owner, order_id, str(payload.get("status") or ""))
    except ValueError:
        return jsonify({"error": "invalid order status"}), 400
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/orders/<order_id>/settle", methods=["POST"])
def order_settle(order_id):
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        order = settle_order(
            owner,
            order_id,
            actual_revenue=payload.get("actual_revenue"),
            actual_cost=payload.get("actual_cost"),
        )
    except ValueError:
        return jsonify({"error": "invalid settlement"}), 400
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/treasury/summary", methods=["GET"])
def treasury_summary():
    owner = _owner_id()
    if owner is None:
        return _auth_error()
    return jsonify(get_financial_summary(owner))
