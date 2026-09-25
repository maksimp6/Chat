import pytest
from flask import Flask

import treasury_identity


def test_optional_identity_ignores_invalid_token(monkeypatch):
    app = Flask(__name__)

    monkeypatch.setattr(treasury_identity, "authenticate_user_token", lambda token: None)
    monkeypatch.setenv("ALICE_OWNER_ID", "server-owner")

    with app.test_request_context(
        "/api/conversations",
        headers={"X-Alice-User-Token": "invalid-token"},
    ):
        assert treasury_identity.get_current_owner_id(required=False) is None


def test_required_identity_rejects_invalid_token(monkeypatch):
    app = Flask(__name__)

    monkeypatch.setattr(treasury_identity, "authenticate_user_token", lambda token: None)

    with app.test_request_context(
        "/api/treasury",
        headers={"X-Alice-User-Token": "invalid-token"},
    ):
        with pytest.raises(treasury_identity.TreasuryIdentityError, match="invalid authenticated owner token"):
            treasury_identity.get_current_owner_id(required=True)
