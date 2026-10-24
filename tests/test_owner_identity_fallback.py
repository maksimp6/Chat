"""LOGOS_OWNER_ID gives an owner only in a deployment declared single-user."""

from economy import treasury_identity


def test_owner_env_alone_does_not_make_every_request_one_owner(monkeypatch):
    monkeypatch.setenv("LOGOS_OWNER_ID", "shared-owner")
    monkeypatch.delenv("LOGOS_SINGLE_USER", raising=False)
    assert treasury_identity.get_current_owner_id(required=False) is None

    monkeypatch.setenv("LOGOS_SINGLE_USER", "false")
    assert treasury_identity.get_current_owner_id(required=False) is None

    monkeypatch.setenv("LOGOS_SINGLE_USER", "true")
    assert treasury_identity.get_current_owner_id() == "shared-owner"
