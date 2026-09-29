import pytest

import app as app_module


@pytest.mark.parametrize(
    "payload",
    [
        [],
        ["unexpected"],
        "unexpected",
        123,
        False,
    ],
)
def test_bootstrap_rejects_non_object_json_without_registering(monkeypatch, payload):
    calls = []

    def should_not_register(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("registration must not run for an invalid request shape")

    monkeypatch.setattr(app_module, "register_anonymous_user", should_not_register)
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().post("/api/users/bootstrap", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": "request body must be an object"}
    assert calls == []


@pytest.mark.parametrize("metadata", [[], "", 0, False])
def test_bootstrap_rejects_falsey_non_object_metadata_without_registering(monkeypatch, metadata):
    calls = []

    def should_not_register(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("registration must not run for invalid metadata")

    monkeypatch.setattr(app_module, "register_anonymous_user", should_not_register)
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().post(
        "/api/users/bootstrap",
        json={
            "installation_id": "ai-generated-shape-regression",
            "metadata": metadata,
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "metadata must be an object"}
    assert calls == []
