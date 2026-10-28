"""HTTP API for the Logos 3D-printing business MVP."""

from flask import Blueprint, jsonify, request

from forge.binding import printing_store as _store
from forge.host import get_current_owner_id
from printing3d import finance, service

printing3d_bp = Blueprint("printing3d", __name__, url_prefix="/api/3d")


def _owner_id():
    return get_current_owner_id()


@printing3d_bp.route("/quote", methods=["POST"])
def quote():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        return jsonify(service.calculate_quote(payload))
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid quote parameters"}), 400


@printing3d_bp.route("/orders", methods=["GET"])
def list_orders():
    owner = _owner_id()
    try:
        return jsonify(
            {
                "orders": service.list_orders(
                    _store(), owner, status=request.args.get("status")
                )
            }
        )
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid order status"}), 400


@printing3d_bp.route("/orders", methods=["POST"])
def create_order():
    owner = _owner_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        return jsonify(service.create_order(_store(), owner, payload)), 201
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid order"}), 400


@printing3d_bp.route("/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    owner = _owner_id()
    order = service.get_order(_store(), owner, order_id)
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/orders/<order_id>/status", methods=["PATCH"])
def update_order_status(order_id):
    owner = _owner_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        order = service.update_order_status(
            _store(), owner, order_id, str(payload.get("status") or "")
        )
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid order status"}), 400
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/orders/<order_id>/settle", methods=["POST"])
def settle_order(order_id):
    owner = _owner_id()
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        order = service.settle_order(
            _store(),
            owner,
            order_id,
            actual_revenue=payload.get("actual_revenue"),
            actual_cost=payload.get("actual_cost"),
        )
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid settlement"}), 400
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(order)


@printing3d_bp.route("/treasury/summary", methods=["GET"])
def get_treasury_summary():
    owner = _owner_id()
    return jsonify(service.get_financial_summary(_store(), owner))


@printing3d_bp.route("/finance/plan", methods=["PUT"])
def set_financing_plan():
    owner = _owner_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be an object"}), 400
    try:
        return jsonify(finance.set_financing_plan(_store(), owner, payload))
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid finance plan"}), 400


@printing3d_bp.route("/finance/status", methods=["GET"])
def get_finance_status():
    owner = _owner_id()
    try:
        return jsonify(
            finance.get_payback_status(_store(), owner, month=request.args.get("month"))
        )
    except (ArithmeticError, ValueError):
        return jsonify({"error": "invalid finance status request"}), 400
