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


@pytest.mark.parametrize("metadata", [[], "", 0, False, None])
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


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("null", "application/json"),
        ("{", "application/json"),
    ],
)
def test_bootstrap_rejects_non_object_or_malformed_json_body_without_registering(
    monkeypatch, body, content_type
):
    calls = []

    def should_not_register(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("registration must not run for invalid JSON")

    monkeypatch.setattr(app_module, "register_anonymous_user", should_not_register)
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().post(
        "/api/users/bootstrap",
        data=body,
        content_type=content_type,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "request body must be an object"}
    assert calls == []


@pytest.mark.parametrize(
    ("request_kwargs", "expected_installation_id"),
    [
        ({}, None),
        ({"json": {"installation_id": "existing-default-behavior"}}, "existing-default-behavior"),
    ],
)
def test_bootstrap_defaults_missing_body_or_metadata_to_empty_object(
    monkeypatch, request_kwargs, expected_installation_id
):
    calls = []

    def register(installation_id, metadata):
        calls.append((installation_id, metadata))
        return {
            "user_id": "anonymous-user",
            "auth_token": "test-token",
        }

    monkeypatch.setattr(app_module, "register_anonymous_user", register)
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().post("/api/users/bootstrap", **request_kwargs)

    assert response.status_code == 200
    assert response.get_json()["user_id"] == "anonymous-user"
    assert calls == [(expected_installation_id, {})]
