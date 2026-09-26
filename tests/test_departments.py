import os
import tempfile
import db
from flask import Flask
from departments import init_department_tables, list_departments, upsert_department, departments_bp


def _app(tmp):
    db.DB_PATH = os.path.join(tmp, "dept.db")
    db.init_db()
    init_department_tables()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(departments_bp)
    return app


def test_department_round_trip():
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _app(tmp)
            d = upsert_department(
                {
                    "id": "government",
                    "name": "Government",
                    "type": "government",
                    "capabilities": ["documents"],
                    "policies": {"approval_required": True},
                }
            )
            assert d["id"] == "government"
            assert d["capabilities"] == ["documents"]
            assert d["policies"]["approval_required"] is True
            assert list_departments()[0]["name"] == "Government"
        finally:
            db.DB_PATH = old


def test_department_admin_is_required_for_mutation():
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            client = _app(tmp).test_client()
            response = client.post("/api/departments", json={"name": "X", "type": "general"})
            assert response.status_code == 401
        finally:
            db.DB_PATH = old

def test_department_validation_errors_are_sanitized(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            monkeypatch.setenv("ALICE_DEPARTMENTS_ADMIN_TOKEN", "dept-admin-token")
            client = _app(tmp).test_client()
            headers = {"X-Department-Admin-Token": "dept-admin-token"}

            create_response = client.post(
                "/api/departments",
                headers=headers,
                json={"name": "", "type": "general"},
            )
            assert create_response.status_code == 400
            create_payload = create_response.get_json()
            assert create_payload["error"] == "invalid_department_request"
            assert "department name is required" not in str(create_payload)

            get_response = client.get("/api/departments/bad!")
            assert get_response.status_code == 400
            get_payload = get_response.get_json()
            assert get_payload["error"] == "invalid_department_id"
            assert "invalid department id" not in str(get_payload)

            update_response = client.put(
                "/api/departments/bad!",
                headers=headers,
                json={"name": "Bad ID", "type": "general"},
            )
            assert update_response.status_code == 400
            update_payload = update_response.get_json()
            assert update_payload["error"] == "invalid_department_request"
            assert "invalid department id" not in str(update_payload)
        finally:
            db.DB_PATH = old

