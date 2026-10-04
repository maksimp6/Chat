import app as app_module


def _get(path):
    return app_module.app.test_client().get(path)


def test_html_and_api_responses_carry_security_headers():
    for path in ("/", "/api/models", "/static/style.css"):
        headers = _get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff", path
        assert headers["X-Frame-Options"] == "DENY", path
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin", path
        assert "microphone=(self)" in headers["Permissions-Policy"], path
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy-Report-Only"], path


def test_csp_only_allows_first_party_sources():
    policy = app_module.CONTENT_SECURITY_POLICY
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "http:" not in policy and "https:" not in policy


def test_routes_can_override_a_security_header():
    flask_app = app_module.app
    with flask_app.test_request_context("/"):
        response = flask_app.response_class("x")
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response = app_module._set_security_headers(response)
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
