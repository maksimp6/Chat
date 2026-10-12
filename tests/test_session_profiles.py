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
    from agent_memory.client import close_memory_clients

    path = tmp_path / "runtime.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(tmp_path / "alice.memory"))
    close_memory_clients()
    db.init_db()
    yield path
    close_memory_clients()


def test_def test_clone_persists_independent_session(runtime_db):
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
