"""RFC 9457 response contract; see docs/api/errors.md."""

import pytest

from invocation.problems import PROBLEM_MIMETYPE, problem


@pytest.fixture()
def flask_app():
    from app import app

    return app


def test_problem_builds_rfc9457_body(flask_app):
    with flask_app.app_context():
        response = problem(
            404,
            "conversation_not_found",
            "Диалог не найден",
            detail="Проверьте ссылку.",
            extensions={"conversation_id": "c1"},
        )
    assert response.status_code == 404
    assert response.mimetype == PROBLEM_MIMETYPE
    assert response.get_json() == {
        "type": "https://alice.pro/problems/conversation_not_found",
        "title": "Диалог не найден",
        "status": 404,
        "error": "conversation_not_found",
        "detail": "Проверьте ссылку.",
        "conversation_id": "c1",
    }


def test_problem_rejects_invalid_input(flask_app):
    with flask_app.app_context():
        with pytest.raises(ValueError):
            problem(200, "ok", "OK")
        with pytest.raises(ValueError):
            problem(400, "Bad-Code", "Bad")
        with pytest.raises(ValueError):
            problem(400, "bad", "Bad", extensions={"status": 500})


def test_unhandled_errors_use_problem_details(flask_app):
    from app import _handle_unexpected_error

    with flask_app.test_request_context("/api/anything"):
        response = _handle_unexpected_error(RuntimeError("secret-internal-detail"))
    assert response.status_code == 500
    assert response.mimetype == PROBLEM_MIMETYPE
    body = response.get_json()
    assert body["type"] == "https://alice.pro/problems/internal_server_error"
    assert body["error"] == "internal_server_error"
    assert body["code"] == "UNHANDLED_EXCEPTION"
    assert "secret-internal-detail" not in response.get_data(as_text=True)
