"""Every client uses the same configured local data identity."""
from flask import Flask
from economy.treasury_identity import get_current_owner_id


def test_shared_identity_ignores_single_user_and_visitor_tokens(monkeypatch):
    monkeypatch.setenv("LOGOS_OWNER_ID", "shared-owner")
    monkeypatch.setenv("LOGOS_SINGLE_USER", "false")
    with Flask(__name__).test_request_context(headers={"X-Logos-User-Token": "obsolete-invalid"}):
        assert get_current_owner_id(required=False) == "shared-owner"


def test_default_identity_without_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("LOGOS_OWNER_ID", raising=False)
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    assert get_current_owner_id() == "default"


def test_single_legacy_ledger_is_selected_and_stays_selected(monkeypatch, tmp_path):
    monkeypatch.delenv("LOGOS_OWNER_ID", raising=False)
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    from storage.db import get_store
    from agent_memory.client import close_memory_clients
    store = get_store()
    with store.transaction() as tx:
        tx.set("treasury/account/old-owner", {"owner_id": "old-owner", "balance": "15"})
    assert get_current_owner_id() == "old-owner"
    with store.transaction() as tx:
        tx.set("treasury/account/other-owner", {"owner_id": "other-owner", "balance": "7"})
    close_memory_clients()
    assert get_current_owner_id() == "old-owner"
    close_memory_clients()
