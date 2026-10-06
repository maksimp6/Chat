import hashlib

import pytest

import db
import user_identity
from agent_memory.file_memory_db import FileMemoryDB
from agent_memory.runtime_store import (
    clear_runtime_memory_db,
    get_runtime_memory_db,
)
from agent_memory.user_identity_store import IDENTITY_KEY, UserIdentityStore


def _configure_paths(monkeypatch, tmp_path):
    memory_path = tmp_path / "alice.memory"
    legacy_path = tmp_path / "legacy.db"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(memory_path))
    monkeypatch.setattr(db, "DB_PATH", str(legacy_path))
    clear_runtime_memory_db(memory_path)
    return memory_path


def _seed_legacy_identity(monkeypatch, tmp_path):
    memory_path = _configure_paths(monkeypatch, tmp_path)
    token = "legacy-user-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    import agent_memory.user_identity_migration as migration

    migration._ensure_legacy_schema()
    conn = db.get_conn()
    try:
        conn.execute(
            """INSERT INTO users
               (id, installation_id, status, metadata_json, auth_token_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-user",
                "legacy-installation-123456",
                "github",
                '{"platform":"legacy"}',
                token_hash,
                10,
                20,
            ),
        )
        conn.execute(
            """INSERT INTO github_accounts
               (github_id, user_id, login, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("42", "legacy-user", "legacy-login", 11, 21),
        )
        conn.commit()
    finally:
        conn.close()

    return memory_path, token


def test_legacy_identity_import_is_verified_and_survives_reopen(monkeypatch, tmp_path):
    memory_path, token = _seed_legacy_identity(monkeypatch, tmp_path)

    user_identity.init_github_accounts_table()

    store = UserIdentityStore(get_runtime_memory_db(memory_path))
    state = store.load()
    assert state is not None
    assert state["users"]["legacy-user"]["installation_id"] == "legacy-installation-123456"
    assert state["github_accounts"]["42"]["login"] == "legacy-login"
    assert state["migration"]["source"] == "legacy-sql"
    assert state["migration"]["user_count"] == 1
    assert state["migration"]["github_account_count"] == 1
    assert user_identity.authenticate_user_token(token) == "legacy-user"
    assert user_identity.get_github_login("legacy-user") == "legacy-login"

    clear_runtime_memory_db(memory_path)
    assert user_identity.authenticate_user_token(token) == "legacy-user"
    assert user_identity.get_github_login("legacy-user") == "legacy-login"


def test_completed_identity_migration_does_not_read_sql(monkeypatch, tmp_path):
    memory_path, token = _seed_legacy_identity(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()

    import agent_memory.user_identity_migration as migration

    monkeypatch.setattr(
        migration,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("SQL accessed after cutover")),
    )

    clear_runtime_memory_db(memory_path)
    assert user_identity.authenticate_user_token(token) == "legacy-user"
    assert user_identity.get_github_login("legacy-user") == "legacy-login"


def test_failed_identity_write_does_not_publish_partial_state(monkeypatch, tmp_path):
    memory_path = _configure_paths(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()

    fixed_user_id = "11111111-1111-1111-1111-111111111111"

    class FixedUUID:
        hex = "1" * 32

        def __str__(self):
            return fixed_user_id

    monkeypatch.setattr(user_identity.uuid, "uuid4", lambda: FixedUUID())

    runtime_db = get_runtime_memory_db(memory_path)
    original_append = runtime_db._append

    def fail_append(record):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(runtime_db, "_append", fail_append)

    with pytest.raises(OSError, match="simulated durable write failure"):
        user_identity.register_anonymous_user("failed-installation-123456", {})

    monkeypatch.setattr(runtime_db, "_append", original_append)
    state = UserIdentityStore(runtime_db).load()
    assert state is not None
    assert fixed_user_id not in state["users"]


def test_identity_aggregate_is_one_durable_record_per_mutation(monkeypatch, tmp_path):
    memory_path = _configure_paths(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()

    runtime_db = get_runtime_memory_db(memory_path)
    before = runtime_db.sequence
    anonymous = user_identity.register_anonymous_user("atomic-installation-123456", {})
    after_bootstrap = runtime_db.sequence
    github = user_identity.sign_in_with_github(77, "octocat", anonymous["user_id"])

    assert after_bootstrap == before + 1
    assert runtime_db.sequence == after_bootstrap + 1
    assert github["user_id"] == anonymous["user_id"]
    assert isinstance(runtime_db.get(IDENTITY_KEY), dict)


def test_identity_runtime_module_has_no_direct_sql_dependency():
    source = open(user_identity.__file__, encoding="utf-8").read()
    assert "get_conn" not in source
    assert "SELECT " not in source
    assert "INSERT " not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source


def test_metadata_sanitizer_handles_nested_lists_and_tuples(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)

    identity = user_identity.register_anonymous_user(
        "nested-metadata-installation-123456",
        {
            "list": [{"token": "drop-me", "safe": 1}],
            "tuple": ({"password": "drop-me-too", "ok": 2},),
        },
    )

    user = user_identity.get_anonymous_user(identity["user_id"])
    assert user is not None
    assert user["metadata"] == {
        "list": [{"safe": 1}],
        "tuple": [{"ok": 2}],
    }


def test_state_fails_closed_if_migration_returns_no_aggregate(monkeypatch):
    class EmptyStore:
        def load(self):
            return None

    monkeypatch.setattr(user_identity, "_store", lambda: EmptyStore())

    with pytest.raises(RuntimeError, match="migration did not produce durable state"):
        user_identity._state()


def test_missing_user_returns_none(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)
    user_identity.init_user_identity_table()

    assert user_identity.get_anonymous_user("missing-user") is None


def test_malformed_metadata_falls_back_to_empty_object(monkeypatch, tmp_path):
    memory_path = _configure_paths(monkeypatch, tmp_path)
    identity = user_identity.register_anonymous_user(
        "malformed-metadata-installation-123456",
        {"safe": True},
    )

    store = UserIdentityStore(get_runtime_memory_db(memory_path))
    state = store.load()
    assert state is not None
    state["users"][identity["user_id"]]["metadata_json"] = "{not-json"
    store.save(state)

    user = user_identity.get_anonymous_user(identity["user_id"])
    assert user is not None
    assert user["metadata"] == {}


def test_existing_github_link_to_missing_user_fails_closed(monkeypatch):
    state = {
        "schema": 1,
        "users": {},
        "github_accounts": {
            "77": {
                "user_id": "missing-user",
                "login": "octocat",
                "created_at": 1,
                "updated_at": 1,
            }
        },
        "migration": {
            "schema": 1,
            "source": "legacy-sql",
            "user_count": 0,
            "github_account_count": 0,
            "digest": "digest",
        },
    }

    class NoSaveStore:
        def save(self, value):
            raise AssertionError("invalid identity must not be saved")

    monkeypatch.setattr(user_identity, "_state", lambda: (NoSaveStore(), state))

    with pytest.raises(ValueError, match="references missing user"):
        user_identity._link_github_account("77", "octocat", None, "token")


def test_legacy_schema_adds_missing_auth_token_hash(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)
    import agent_memory.user_identity_migration as migration

    conn = db.get_conn()
    try:
        conn.execute(
            """CREATE TABLE users (
                id TEXT PRIMARY KEY,
                installation_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'anonymous',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()

    migration._ensure_legacy_schema()

    conn = db.get_conn()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()
    assert "auth_token_hash" in columns


def test_legacy_schema_rolls_back_on_operational_error(monkeypatch):
    import agent_memory.user_identity_migration as migration
    from db_backend import OperationalError

    class BrokenConnection:
        rolled_back = False
        closed = False

        def execute(self, sql):
            raise OperationalError("schema failed")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    conn = BrokenConnection()
    monkeypatch.setattr(migration, "get_conn", lambda: conn)

    with pytest.raises(OperationalError, match="schema failed"):
        migration._ensure_legacy_schema()

    assert conn.rolled_back is True
    assert conn.closed is True


@pytest.mark.parametrize("mode", ["missing", "state", "digest"])
def test_identity_migration_verification_fails_closed(monkeypatch, mode):
    import agent_memory.user_identity_migration as migration
    from agent_memory.user_identity_store import migrated_identity_state

    users = {
        "user-a": {
            "installation_id": "installation-a-123456",
            "status": "anonymous",
            "metadata_json": "{}",
            "auth_token_hash": None,
            "created_at": 1,
            "updated_at": 1,
        }
    }
    accounts = {}
    expected = migrated_identity_state(users, accounts)
    monkeypatch.setattr(migration, "_legacy_identity", lambda: (users, accounts))

    class VerificationStore:
        def __init__(self):
            self.reads = 0

        def load(self):
            self.reads += 1
            if self.reads == 1:
                return None
            if mode == "missing":
                return None
            if mode == "state":
                bad = migrated_identity_state({}, {})
                return bad
            bad = migrated_identity_state(users, accounts)
            bad["migration"]["digest"] = "wrong"
            return bad

        def save(self, state):
            assert state == expected
            return 1

    with pytest.raises(RuntimeError, match="migration verification failed"):
        migration.ensure_user_identity_migrated(VerificationStore())


def _valid_identity_state():
    from agent_memory.user_identity_store import migrated_identity_state

    users = {
        "user-a": {
            "installation_id": "installation-a-123456",
            "status": "anonymous",
            "metadata_json": "{}",
            "auth_token_hash": "hash-a",
            "created_at": 1,
            "updated_at": 1,
        },
        "user-b": {
            "installation_id": "installation-b-123456",
            "status": "github",
            "metadata_json": "{}",
            "auth_token_hash": "hash-b",
            "created_at": 2,
            "updated_at": 2,
        },
    }
    accounts = {
        "42": {
            "user_id": "user-b",
            "login": "octocat",
            "created_at": 2,
            "updated_at": 2,
        }
    }
    return migrated_identity_state(users, accounts)


def test_identity_store_rejects_invalid_record_shapes(tmp_path):
    from agent_memory.user_identity_store import UserIdentityStore

    store = UserIdentityStore(FileMemoryDB(tmp_path / "alice.memory"))

    with pytest.raises(ValueError, match="invalid user identity record"):
        store._user_record("bad")
    with pytest.raises(ValueError, match="invalid user identity record"):
        store._user_record(
            {
                "installation_id": "",
                "status": "unknown",
                "metadata_json": 1,
                "auth_token_hash": [],
                "created_at": "x",
                "updated_at": None,
            }
        )
    with pytest.raises(ValueError, match="invalid github identity record"):
        store._github_record("bad")
    with pytest.raises(ValueError, match="invalid github identity record"):
        store._github_record(
            {
                "user_id": "",
                "login": "",
                "created_at": "x",
                "updated_at": None,
            }
        )
    with pytest.raises(ValueError, match="invalid user identity migration marker"):
        store._migration("bad")
    with pytest.raises(ValueError, match="invalid user identity migration marker"):
        store._migration(
            {
                "schema": 2,
                "source": "",
                "user_count": -1,
                "github_account_count": -1,
                "digest": "",
            }
        )


def test_identity_store_rejects_invalid_aggregate_shapes(tmp_path):
    from agent_memory.user_identity_store import IDENTITY_KEY, UserIdentityStore

    raw = FileMemoryDB(tmp_path / "alice.memory")
    store = UserIdentityStore(raw)

    raw.put(IDENTITY_KEY, "bad")
    with pytest.raises(ValueError, match="invalid user identity aggregate"):
        store.load()

    raw.put(
        IDENTITY_KEY,
        {
            "schema": 1,
            "users": [],
            "github_accounts": {},
            "migration": {},
        },
    )
    with pytest.raises(ValueError, match="invalid user identity aggregate"):
        store.load()

    raw.put(
        IDENTITY_KEY,
        {
            "schema": 1,
            "users": {
                "": {
                    "installation_id": "installation-a-123456",
                    "status": "anonymous",
                    "metadata_json": "{}",
                    "auth_token_hash": None,
                    "created_at": 1,
                    "updated_at": 1,
                }
            },
            "github_accounts": {},
            "migration": {
                "schema": 1,
                "source": "legacy-sql",
                "user_count": 0,
                "github_account_count": 0,
                "digest": "digest",
            },
        },
    )
    with pytest.raises(ValueError, match="invalid user identity aggregate"):
        store.load()


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("empty-user", "invalid user identity record"),
        ("duplicate-installation", "duplicate user installation id"),
        ("duplicate-token", "duplicate user auth token hash"),
        ("missing-user-link", "github account references missing user"),
        ("multiple-links", "user has multiple github accounts"),
        ("user-count", "invalid user identity migration counts"),
        ("account-count", "invalid user identity migration counts"),
    ],
)
def test_identity_store_rejects_invariant_violations(tmp_path, mutation, message):
    import copy

    from agent_memory.user_identity_store import UserIdentityStore

    store = UserIdentityStore(FileMemoryDB(tmp_path / f"{mutation}.memory"))
    state = copy.deepcopy(_valid_identity_state())

    if mutation == "empty-user":
        state["users"][""] = state["users"].pop("user-a")
    elif mutation == "duplicate-installation":
        state["users"]["user-b"]["installation_id"] = state["users"]["user-a"]["installation_id"]
    elif mutation == "duplicate-token":
        state["users"]["user-b"]["auth_token_hash"] = state["users"]["user-a"]["auth_token_hash"]
    elif mutation == "missing-user-link":
        state["github_accounts"]["42"]["user_id"] = "missing"
    elif mutation == "multiple-links":
        state["github_accounts"]["43"] = {
            "user_id": "user-b",
            "login": "second",
            "created_at": 3,
            "updated_at": 3,
        }
    elif mutation == "user-count":
        state["migration"]["user_count"] = 99
    elif mutation == "account-count":
        state["migration"]["github_account_count"] = 99

    with pytest.raises(ValueError, match=message):
        store.save(state)


def test_identity_store_allows_user_without_token_hash(tmp_path):
    from agent_memory.user_identity_store import UserIdentityStore

    store = UserIdentityStore(FileMemoryDB(tmp_path / "no-token.memory"))
    state = _valid_identity_state()
    state["users"]["user-a"]["auth_token_hash"] = None

    store.save(state)

    recovered = store.load()
    assert recovered is not None
    assert recovered["users"]["user-a"]["auth_token_hash"] is None
