import os
import tempfile

import db

from partner_relations import (
    PARTNER_TOOLS,
    add_contact,
    create_followup,
    create_partner,
    get_partner,
    init_partner_relations_tables,
    prepare_message,
    update_contact,
    update_status,
)
from universal_tool_platform import UniversalToolExecutor, UniversalToolCall
from tool_registry import ToolRegistry


def _ctx(user_id):
    return {
        "_universal_context": {
            "call": UniversalToolCall(tool_name="test", arguments={}, user_id=user_id)
        }
    }


def test_partner_data_is_isolated_by_trusted_owner(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "partners.db"))
            init_partner_relations_tables()

            first = create_partner({"name": "Alpha", "organization": "One"}, _ctx("user-a"))
            second = create_partner({"name": "Beta", "organization": "Two"}, _ctx("user-b"))

            assert get_partner({"partner_id": first["id"]}, _ctx("user-a"))["partner"]["name"] == "Alpha"
            try:
                get_partner({"partner_id": first["id"]}, _ctx("user-b"))
            except ValueError as exc:
                assert str(exc) == "partner not found"
            else:
                raise AssertionError("cross-owner partner access was allowed")

            assert get_partner({"partner_id": second["id"]}, _ctx("user-b"))["partner"]["name"] == "Beta"
        finally:
            db.DB_PATH = old


def test_contacts_messages_status_and_followups_round_trip(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "partners.db"))
            init_partner_relations_tables()
            ctx = _ctx("owner-1")
            partner = create_partner({"name": "Acme", "status": "lead"}, ctx)
            contact = add_contact({
                "partner_id": partner["id"],
                "name": "Ada",
                "role": "CTO",
                "email": "ada@example.test",
            }, ctx)["contact"]
            updated = update_contact({
                "contact_id": contact["id"],
                "name": "Ada Lovelace",
                "role": "CTO",
                "email": "ada@example.test",
            }, ctx)["contact"]
            assert updated["name"] == "Ada Lovelace"

            message = prepare_message({
                "partner_id": partner["id"],
                "body": "Добрый день",
                "subject": "Встреча",
            }, ctx)["message"]
            followup = create_followup({
                "partner_id": partner["id"],
                "title": "Позвонить",
                "due_at": 2_000_000_000,
            }, ctx)
            status = update_status({"partner_id": partner["id"], "status": "negotiating"}, ctx)

            details = get_partner({"partner_id": partner["id"]}, ctx)["partner"]
            assert details["contacts"][0]["name"] == "Ada Lovelace"
            assert details["messages"][0]["id"] == message["id"]
            assert details["messages"][0]["status"] == "prepared"
            assert details["followups"][0]["id"] == followup["id"]
            assert status["status"] == "negotiating"
        finally:
            db.DB_PATH = old


def test_partner_tools_have_approval_boundary_and_universal_registration():
    assert PARTNER_TOOLS["partner.message.send"]["requires_approval"] is True
    assert PARTNER_TOOLS["partner.message.send"]["read_only"] is False
    assert PARTNER_TOOLS["partner.thread.get"]["requires_approval"] is False

    registry = ToolRegistry()
    definitions = registry.get_universal_definitions()
    by_name = {item["name"]: item for item in definitions}
    assert "partner.create" in by_name
    assert "partner.message.send" in by_name
    assert "mcp" in by_name["partner.message.send"]["supported_transports"]
