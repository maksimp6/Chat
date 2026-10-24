import app as app_module


def test_html_shell_is_not_cached():
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


def test_static_assets_revalidate_instead_of_pinning_stale_content():
    response = app_module.app.test_client().get("/static/style.css")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"


def test_api_responses_are_not_cached():
    response = app_module.app.test_client().get("/api/models")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
