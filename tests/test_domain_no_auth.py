"""Shared domain data remains accessible without visitor authorization."""

import pytest
from flask import Flask

from agent_memory.client import close_memory_clients
import storage.db as db
from organization import departments, government, partner_relations as partners
from forge import routes as printing_routes
from printing3d import finance, service


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "domain.memory"))
    monkeypatch.setenv("LOGOS_OWNER_ID", "local-shared")
    monkeypatch.setenv("LOGOS_DEPARTMENTS_ADMIN_TOKEN", "unused-obsolete-token")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    db.init_db()
    app = Flask(__name__)
    app.testing = True
    for blueprint in [departments.departments_bp, government.government_bp, partners.partner_relations_bp, printing_routes.printing3d_bp]:
        app.register_blueprint(blueprint)
    yield app.test_client()
    close_memory_clients()


def test_department_crud_has_no_admin_gate(client):
    response = client.post("/api/departments", json={"id": "shared", "name": "Shared"})
    assert response.status_code == 201
    assert client.put("/api/departments/shared", json={"name": "Updated"}).status_code == 200
    assert client.get("/api/departments/shared").get_json()["department"]["name"] == "Updated"
    assert client.delete("/api/departments/shared").status_code == 200


def test_preexisting_government_case_is_shared_but_not_submitted(client):
    case = government.create_case("IP_REGISTRATION", "old-user")
    response = client.get(f"/api/government/cases/{case['case_id']}")
    assert response.status_code == 200
    assert response.get_json()["case"]["user_id"] == "old-user"
    assert not response.get_json()["case"]["user_approved"]
    assert client.get("/api/government/cases/missing").status_code == 404


def test_preexisting_partner_and_contact_crud_preserves_original_keys(client, monkeypatch):
    with monkeypatch.context() as seed:
        seed.setattr(partners, "_trusted_owner", lambda cfg=None: "old-user")
        partner = partners.create_partner({"name": "Old partner"})
        contact = partners.add_contact({"partner_id": partner["id"], "name": "Old contact"})["contact"]
    key = partners._key("partner", partner["id"], "old-user")
    contact_key = partners._key("contact", contact["id"], "old-user")
    assert partners.get_partner({"partner_id": partner["id"]})["partner"]["name"] == "Old partner"
    partners.update_partner({"partner_id": partner["id"], "name": "Shared update"})
    assert db.get_store().get(key)["name"] == "Shared update"
    assert db.get_store().get(partners._key("partner", partner["id"], "local-shared")) is None
    partners.delete_contact({"contact_id": contact["id"]})
    assert db.get_store().get(contact_key) is None
    new_contact = partners.add_contact({"partner_id": partner["id"], "name": "Shared contact"})["contact"]
    assert new_contact["partner_id"] == partner["id"]


def test_preexisting_print_order_can_be_read_updated_and_settled_locally(client):
    store = db.get_store()
    order = service.create_order(store, "old-user", {"title": "Fixture part"})
    response = client.get(f"/api/3d/orders/{order['id']}")
    assert response.status_code == 200
    assert response.get_json()["owner_id"] == "old-user"
    response = client.patch(f"/api/3d/orders/{order['id']}/status", json={"status": "accepted"})
    assert response.status_code == 200
    response = client.post(f"/api/3d/orders/{order['id']}/settle", json={"actual_revenue": 30, "actual_cost": 10})
    assert response.status_code == 200
    assert response.get_json()["status"] == "paid"
    summary = client.get("/api/3d/treasury/summary").get_json()
    assert summary["currencies"][0]["settled_profit"] == 20
    assert client.get("/api/3d/orders/missing").status_code == 404


def test_preexisting_finance_plan_is_available_to_shared_identity(client):
    plan = finance.set_financing_plan(db.get_store(), "old-user", {"printer_model": "Fixture", "equipment_price": 100})
    response = client.get("/api/3d/finance/status")
    assert response.status_code == 200
    assert response.get_json()["plan"] == plan
