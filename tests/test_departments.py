import os
import tempfile
import db
from flask import Flask
from departments import init_department_tables, list_departments, upsert_department, departments_bp

def _app(tmp):
    db.DB_PATH = os.path.join(tmp,"dept.db")
    db.init_db()
    init_department_tables()
    app=Flask(__name__); app.config["TESTING"]=True; app.register_blueprint(departments_bp)
    return app

def test_department_round_trip():
    old=db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _app(tmp)
            d=upsert_department({"id":"government","name":"Government","type":"government","capabilities":["documents"],"policies":{"approval_required":True}})
            assert d["id"]=="government"
            assert d["capabilities"]==["documents"]
            assert d["policies"]["approval_required"] is True
            assert list_departments()[0]["name"]=="Government"
        finally:
            db.DB_PATH=old

def test_department_admin_is_required_for_mutation():
    old=db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            client=_app(tmp).test_client()
            response=client.post("/api/departments",json={"name":"X","type":"general"})
            assert response.status_code==401
        finally:
            db.DB_PATH=old
