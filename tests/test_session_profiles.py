import pytest

from session_profiles import clone_profile, get_profile, list_profiles, session_profile


def test_builtin_profiles_are_available():
    profiles = list_profiles()
    assert {profile["id"] for profile in profiles} == {
        "developer",
        "researcher",
        "assistant",
        "terminal",
        "agent",
    }
    assert all(profile["template"] is True for profile in profiles)


def test_missing_profile_returns_none():
    assert get_profile("does-not-exist") is None


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    import db
    import session_manager
    import runtime_migrations

    path = tmp_path / "profiles.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    return path


def test_clone_persists_independent_session(runtime_db):
    result = clone_profile("developer", "My Developer")

    session = result["session"]
    profile = result["profile"]

    assert session["id"] == profile["id"]
    assert session["status"] == "active"
    assert session["metadata"]["profile_id"] == "developer"
    assert profile["template"] is False
    assert profile["name"] == "My Developer"
    assert session_profile(session["id"])["id"] == profile["id"]

    profile["tools"].append("extra")
    fresh = get_profile("developer")
    assert fresh["tools"] == ["filesystem", "terminal", "git"]
