"""HTTP API of the treasury: accounts and the demo top-up."""

from __future__ import annotations

from flask import Flask, jsonify, request

from economy.host import get_store
from economy.treasury import demo_top_up, get_account
from economy.treasury_identity import TreasuryIdentityError, get_current_owner_id


def treasury_account():
    owner_id = str(request.args.get("owner_id") or get_current_owner_id())
    return jsonify(get_account(owner_id))


def treasury_accounts():
    """Keep all pre-existing ledgers discoverable without combining their balances."""
    return jsonify(
        {"accounts": list(get_store().items(prefix="treasury/account/").values())}
    )


def treasury_top_up():
    data = request.get_json(silent=True) or {}
    try:
        owner_id = get_current_owner_id()
        account = demo_top_up(
            owner_id, data.get("amount"), data.get("description", "Demo top-up")
        )
        return jsonify({"demo": True, "account": account}), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


def install(app: Flask) -> None:
    """Serve the treasury API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/treasury/account", view_func=treasury_account, methods=["GET"]
    )
    app.add_url_rule(
        "/api/treasury/accounts", view_func=treasury_accounts, methods=["GET"]
    )
    app.add_url_rule(
        "/api/treasury/top-up", view_func=treasury_top_up, methods=["POST"]
    )
