import os
import tempfile

import pytest

import storage.db as db
from organization.partner_relations import (
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
from tool_providers.universal_tool_platform import UniversalToolExecutor, UniversalToolCall
from tool_providers.registry import ToolRegistry


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

            assert (
                get_partner({"partner_id": first["id"]}, _ctx("user-a"))["partner"]["name"]
                == "Alpha"
            )
            try:
                get_partner({"partner_id": first["id"]}, _ctx("user-b"))
            except ValueError as exc:
                assert str(exc) == "partner not found"
            else:
                raise AssertionError("cross-owner partner access was allowed")

            assert (
                get_partner({"partner_id": second["id"]}, _ctx("user-b"))["partner"]["name"]
                == "Beta"
            )
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
            contact = add_contact(
                {
                    "partner_id": partner["id"],
                    "name": "Ada",
                    "role": "CTO",
                    "email": "ada@example.test",
                },
                ctx,
            )["contact"]
            updated = update_contact(
                {
                    "contact_id": contact["id"],
                    "name": "Ada Lovelace",
                    "role": "CTO",
                    "email": "ada@example.test",
                },
                ctx,
            )["contact"]
            assert updated["name"] == "Ada Lovelace"

            message = prepare_message(
                {
                    "partner_id": partner["id"],
                    "body": "Добрый день",
                    "subject": "Встреча",
                },
                ctx,
            )["message"]
            followup = create_followup(
                {
                    "partner_id": partner["id"],
                    "title": "Позвонить",
                    "due_at": 2_000_000_000,
                },
                ctx,
            )
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


def test_a_partial_contact_update_keeps_the_fields_it_does_not_name(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "partners.db"))
            init_partner_relations_tables()
            ctx = _ctx("owner-1")
            partner = create_partner({"name": "Acme"}, ctx)
            contact = add_contact(
                {
                    "partner_id": partner["id"],
                    "name": "Ada",
                    "email": "ada@example.test",
                    "phone": "+7 900 000",
                },
                ctx,
            )["contact"]

            updated = update_contact({"contact_id": contact["id"], "email": ""}, ctx)["contact"]

            assert updated["email"] == ""
            assert updated["phone"] == "+7 900 000"
            assert updated["name"] == "Ada"
        finally:
            db.DB_PATH = old


def test_http_bodies_follow_the_tool_field_rules():
    from organization.partner_relations import _http_arguments

    assert _http_arguments("partner.create", {"name": "Acme"}) == {"name": "Acme"}
    with pytest.raises(ValueError, match="invalid name"):
        _http_arguments("partner.create", {"name": "x" * 201})
    with pytest.raises(ValueError, match="invalid status"):
        _http_arguments("partner.create", {"status": "deleted"})
    with pytest.raises(ValueError, match="JSON object"):
        _http_arguments("partner.create", ["Acme"])


def test_a_non_object_partner_body_is_a_client_error(monkeypatch):
    from flask import Flask

    from organization import partner_relations

    monkeypatch.setattr(partner_relations, "get_current_owner_id", lambda required=True: "owner-1")
    app = Flask(__name__)
    app.register_blueprint(partner_relations.partner_relations_bp)

    response = app.test_client().post("/api/partners", json=["Acme"])

    assert response.status_code == 400


def test_partner_metadata_is_size_limited():
    with pytest.raises(ValueError, match="metadata is too large"):
        create_partner({"name": "Acme", "metadata": {"blob": "x" * 9000}}, _ctx("owner-1"))


def test_owners_may_use_the_same_client_chosen_id(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "partners.db"))
            init_partner_relations_tables()
            create_partner({"id": "shared-id", "name": "Alpha"}, _ctx("owner-a"))
            create_partner({"id": "shared-id", "name": "Beta"}, _ctx("owner-b"))

            first = get_partner({"partner_id": "shared-id"}, _ctx("owner-a"))["partner"]
            second = get_partner({"partner_id": "shared-id"}, _ctx("owner-b"))["partner"]
            assert (first["name"], second["name"]) == ("Alpha", "Beta")
            with pytest.raises(ValueError, match="already exists"):
                create_partner({"id": "shared-id", "name": "Again"}, _ctx("owner-a"))
        finally:
            db.DB_PATH = old


def test_rows_stored_under_the_unscoped_key_move_to_the_owner_key(monkeypatch):
    from organization import partner_relations

    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "partners.db"))
            monkeypatch.setattr(partner_relations, "_owner_keys_migrated", False)
            legacy = {
                "id": "legacy-1",
                "owner_id": "owner-1",
                "name": "Old",
                "organization": "",
                "email": "",
                "phone": "",
                "status": "lead",
                "notes": "",
                "metadata": {},
                "created_at": 1,
                "updated_at": 1,
            }
            with db.get_store().transaction() as tx:
                tx.set("partners/partner/legacy-1", legacy)

            init_partner_relations_tables()

            partner = get_partner({"partner_id": "legacy-1"}, _ctx("owner-1"))["partner"]
            assert partner["name"] == "Old"
            assert db.get_store().get("partners/partner/legacy-1") is None
        finally:
            db.DB_PATH = old
