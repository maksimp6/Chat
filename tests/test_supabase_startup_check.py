from urllib.error import HTTPError

import supabase_startup_check as checker


def test_missing_credentials_is_disabled(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert checker.check_supabase_trace_mirror() == "disabled"


def test_invalid_url_is_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://example.com")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    assert checker.check_supabase_trace_mirror() == "error"


def test_reachable_endpoint_is_ready(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(checker, "urlopen", lambda request, timeout: Response())
    assert checker.check_supabase_trace_mirror() == "ready"


def test_missing_table_is_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    def fail(request, timeout):
        raise HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(checker, "urlopen", fail)
    assert checker.check_supabase_trace_mirror() == "error"
