"""Identity works natively on the one durable Kristall instance."""

import pytest

import storage.db as db
import identity.user_identity as user_identity
from agent_memory.client import close_memory_clients, get_memory_client
from agent_memory.user_identity_store import IDENTITY_KEY, UserIdentityStore
from kristall.store import KristallError


def _configure(monkeypatch, tmp_path):
    path = tmp_path / "logos.memory"
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    return path


def test_identity_bootstrap_and_token_survive_reopen(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()
    first = user_identity.register_anonymous_user("installation-1234567890", {})
    assert user_identity.authenticate_user_token(first["auth_token"]) == first["user_id"]
    linked = user_identity.sign_in_with_github(42, "octocat", first["user_id"])
    assert linked["user_id"] == first["user_id"]
    close_memory_clients(path)
    assert user_identity.authenticate_user_token(linked["auth_token"]) == linked["user_id"]
    assert user_identity.get_github_login(linked["user_id"]) == "octocat"
    assert UserIdentityStore(get_memory_client(path)).load()["schema"] == 1


def test_bootstrap_writes_no_sql_or_migration_marker(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    user_identity.init_github_accounts_table()
    store = UserIdentityStore(get_memory_client(path))
    state = store.load()
    assert state == {"schema": 1, "users": {}, "github_accounts": {}}
    assert "migration" not in state
    assert get_memory_client(path).last_commit.sequence == 1


def test_identity_write_failure_does_not_publish_partial_record(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()
    runtime = get_memory_client(path)
    before = runtime.last_commit.sequence

    def fail_append(_frame):
        raise KristallError("simulated journal write failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime._engine, "_append_frame", fail_append)
        with pytest.raises(KristallError, match="simulated journal"):
            user_identity.register_anonymous_user("installation-write-failure", {})
    assert runtime.last_commit.sequence == before
    assert UserIdentityStore(runtime).load()["users"] == {}


def test_identity_mutations_are_atomic(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()
    runtime = get_memory_client(path)
    before = runtime.last_commit.sequence
    anon = user_identity.register_anonymous_user("installation-atomic-test", {})
    assert runtime.last_commit.sequence == before + 1
    linked = user_identity.sign_in_with_github(77, "octocat", anon["user_id"])
    assert runtime.last_commit.sequence == before + 2
    assert linked["user_id"] == anon["user_id"]
    assert isinstance(runtime.get(IDENTITY_KEY), dict)


def test_identity_validation_rejects_bad_shape(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    raw = get_memory_client(path)
    raw.set(IDENTITY_KEY, {"schema": 1, "users": [], "github_accounts": {}})
    raw.commit()
    with pytest.raises(ValueError, match="invalid user identity aggregate"):
        UserIdentityStore(raw).load()


def test_identity_validation_rejects_dangling_github_link(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    store = UserIdentityStore(get_memory_client(path))
    with pytest.raises(ValueError, match="references missing user"):
        store.save(
            {
                "schema": 1,
                "users": {},
                "github_accounts": {
                    "42": {
                        "user_id": "missing",
                        "login": "octocat",
                        "created_at": 1,
                        "updated_at": 1,
                    }
                },
            }
        )


def test_identity_metadata_sanitizer_and_invalid_user(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = user_identity.register_anonymous_user(
        "installation-metadata-test",
        {"list": [{"token": "hidden", "ok": 1}], "platform": "android"},
    )
    assert user_identity.get_anonymous_user(first["user_id"])["metadata"] == {
        "list": [{"ok": 1}],
        "platform": "android",
    }
    assert user_identity.get_anonymous_user("missing") is None
