from scripts.ci_platform_changes import classify


def test_android_change_does_not_trigger_backend_database_or_infra():
    assert classify(["android/app/src/main/Main.kt"]) == {
        "web": False,
        "backend": False,
        "android": True,
        "infra": False,
        "database": False,
    }


def test_infra_change_does_not_trigger_android_or_database():
    result = classify(["cloud/cloudru/registry_client.py"])
    assert result["infra"] is True
    assert result["backend"] is False
    assert result["android"] is False
    assert result["database"] is False


def test_database_change_is_backend_but_not_android():
    result = classify(["requirements-postgres.txt"])
    assert result["database"] is True
    assert result["backend"] is False
    assert result["android"] is False


def test_web_is_the_explicit_cross_platform_integration_surface():
    result = classify(["static/app.js"])
    assert result["web"] is True
    assert result["backend"] is True
    assert result["android"] is True


def test_docs_only_change_runs_no_platform_suite():
    assert classify(["docs/README.md"]) == {
        "web": False,
        "backend": False,
        "android": False,
        "infra": False,
        "database": False,
    }


def test_platform_router_test_does_not_trigger_application_suite():
    result = classify(["tests/test_ci_platform_changes.py"])
    assert result["backend"] is False
    assert result["android"] is False
    assert result["database"] is False
