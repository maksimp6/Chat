import json
from unittest.mock import patch

import pytest

from invocation.context import InvocationContext
from invocation.trace import create_invocation_trace
from provider_credentials import (
    CredentialError,
    NoActiveCredentialError,
    get_secret_management_ref,
    resolve_secret_management_value,
    rollback_secret_management_ref,
    set_secret_management_ref,
)
from trace_manager import ExecutionTrace, bind_current_trace, get_current_trace, reset_current_trace


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import db

    db.DB_PATH = str(tmp_path / "secrets.db")
    db.init_db()
    conn = db.get_conn()
    yield conn
    conn.close()


class FakeSecretManagementClient:
    """Stands in for CloudRuSecretManagementClient without any network access."""

    def __init__(self, values):
        self.values = values
        self.calls = []

    def get_secret_value(self, secret_id, version_id):
        self.calls.append((secret_id, version_id))
        try:
            return self.values[(secret_id, version_id)]
        except KeyError:
            raise RuntimeError(f"no fake value for {secret_id}:{version_id}")


def test_set_and_get_ref_round_trips(isolated_db):
    ref = set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    assert ref.purpose == "alice_short_token"
    assert ref.secret_id == "secret-1"
    assert ref.pinned_version_id == "v1"
    assert ref.previous_version_id is None

    fetched = get_secret_management_ref(isolated_db, "alice_short_token")
    assert fetched == ref


def test_missing_ref_returns_none(isolated_db):
    assert get_secret_management_ref(isolated_db, "unknown_purpose") is None


def test_switching_version_remembers_previous_for_rollback(isolated_db):
    set_secret_management_ref(isolated_db, "github_oauth_client_secret", "secret-1", "v1")
    switched = set_secret_management_ref(
        isolated_db, "github_oauth_client_secret", "secret-1", "v2"
    )

    assert switched.pinned_version_id == "v2"
    assert switched.previous_version_id == "v1"


def test_switching_to_a_different_secret_id_does_not_offer_rollback(isolated_db):
    set_secret_management_ref(isolated_db, "provider_credential:yandex", "secret-1", "v1")
    switched = set_secret_management_ref(
        isolated_db, "provider_credential:yandex", "secret-2", "v1"
    )
    assert switched.previous_version_id is None


def test_rollback_restores_prior_pinned_version(isolated_db):
    set_secret_management_ref(isolated_db, "alice_database_url", "secret-1", "v1")
    set_secret_management_ref(isolated_db, "alice_database_url", "secret-1", "v2")

    rolled_back = rollback_secret_management_ref(isolated_db, "alice_database_url")
    assert rolled_back.pinned_version_id == "v1"
    assert rolled_back.previous_version_id == "v2"

    # Rollback is itself explicit and reversible: rolling back again returns to v2.
    rolled_back_again = rollback_secret_management_ref(isolated_db, "alice_database_url")
    assert rolled_back_again.pinned_version_id == "v2"
    assert rolled_back_again.previous_version_id == "v1"


def test_rollback_without_prior_version_fails_closed(isolated_db):
    set_secret_management_ref(isolated_db, "alice_database_url", "secret-1", "v1")
    with pytest.raises(CredentialError):
        rollback_secret_management_ref(isolated_db, "alice_database_url")


def test_rollback_without_any_ref_fails_closed(isolated_db):
    with pytest.raises(NoActiveCredentialError):
        rollback_secret_management_ref(isolated_db, "never_configured")


def test_resolve_requires_a_configured_ref(isolated_db):
    with pytest.raises(NoActiveCredentialError):
        resolve_secret_management_value(isolated_db, "alice_short_token")


def test_resolve_fetches_the_pinned_version_only(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v2")
    client = FakeSecretManagementClient({("secret-1", "v2"): "the-plaintext-token"})

    value = resolve_secret_management_value(isolated_db, "alice_short_token", client=client)

    assert value == "the-plaintext-token"
    assert client.calls == [("secret-1", "v2")]


def test_resolve_after_rollback_uses_restored_version(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v2")
    rollback_secret_management_ref(isolated_db, "alice_short_token")

    client = FakeSecretManagementClient({("secret-1", "v1"): "rolled-back-value"})
    assert (
        resolve_secret_management_value(isolated_db, "alice_short_token", client=client)
        == "rolled-back-value"
    )


def test_ref_table_only_ever_stores_identifiers_not_a_value_column(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    columns = {
        row["name"]
        for row in isolated_db.execute("PRAGMA table_info(secret_management_refs)").fetchall()
    }
    assert columns == {
        "purpose",
        "secret_id",
        "pinned_version_id",
        "previous_version_id",
        "updated_at",
    }


def test_secret_value_never_reaches_trace_even_when_a_later_error_is_recorded(isolated_db):
    """Regression for issue #477: nested errors after resolution must not leak."""
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    client = FakeSecretManagementClient({("secret-1", "v1"): "must-not-leak-anywhere"})
    context = InvocationContext("session", "conversation", "invocation", "secret-trace")
    trace = create_invocation_trace(context)
    token = bind_current_trace(trace)
    assert get_current_trace() is trace

    try:
        opaque = resolve_secret_management_value(isolated_db, "alice_short_token", client=client)
        assert opaque == "must-not-leak-anywhere"
        trace.add_event("opaque_value", {"value": opaque})

        raise RuntimeError(f"downstream failure: {opaque}")
    except RuntimeError as exc:
        trace.record_error("test.nested", f"downstream failure: {opaque}", exception=exc)
    finally:
        reset_current_trace(token)

    data = trace.make_snapshot()
    snapshot = json.dumps(data, ensure_ascii=False)
    assert "must-not-leak-anywhere" not in snapshot
    assert data["events"][-2]["payload"]["value"] == "<redacted>"
    assert data["errors"][0]["error"] == "downstream failure: <redacted>"
    assert any(
        frame["locals"].get("opaque") == "<redacted>"
        for frame in data["errors"][0]["python_exception"]["frames"]
    )


def test_trace_redacts_short_registered_values_inside_text():
    trace = ExecutionTrace(trace_id="short-secret-redaction-test")
    trace.register_sensitive_value(None)
    trace.register_sensitive_value("")
    trace.register_sensitive_value("abc")

    trace.add_event(
        "message",
        {"text": "abc", "inline": "prefix abc suffix", "tuple": ("abc",), "set": {"abc"}},
    )

    payload = trace.make_snapshot()["events"][0]["payload"]
    assert payload["text"] == "<redacted>"
    assert payload["inline"] == "prefix abc suffix"
    assert payload["tuple"] == ["<redacted>"]
    assert payload["set"] == ["<redacted>"]


def test_trace_redacts_long_secret_before_repr_truncation():
    secret = "long-secret-value-" * 300
    trace = ExecutionTrace(trace_id="long-secret-redaction-test")
    trace.register_sensitive_value(secret)

    def capture_secret():
        opaque = secret
        raise RuntimeError("downstream failure")

    trace.add_event("long_secret", {"value": secret})
    trace.record_error("test.long-secret", f"secret in error: {secret}")
    try:
        capture_secret()
    except RuntimeError as exc:
        trace.record_error("test.long-secret", "downstream failure", exception=exc)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert secret not in snapshot
    assert '"opaque": "<redacted>"' in snapshot


def test_repinning_current_version_preserves_rollback_target(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v2")

    repeated = set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v2")

    assert repeated.pinned_version_id == "v2"
    assert repeated.previous_version_id == "v1"


@pytest.mark.parametrize(
    "purpose,secret_id,version_id",
    [
        ("", "secret-1", "v1"),
        ("alice_short_token", "", "v1"),
        ("alice_short_token", "secret-1", ""),
    ],
)
def test_set_ref_rejects_missing_identifiers(isolated_db, purpose, secret_id, version_id):
    with pytest.raises(ValueError, match="purpose, secret_id and version_id are required"):
        set_secret_management_ref(isolated_db, purpose, secret_id, version_id)


def test_resolve_constructs_default_backend_client(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    fake = FakeSecretManagementClient({("secret-1", "v1"): "resolved-default"})
    with patch(
        "cloud.cloudru.secret_management.CloudRuSecretManagementClient",
        return_value=fake,
    ) as client_type:
        value = resolve_secret_management_value(isolated_db, "alice_short_token")

    assert value == "resolved-default"
    client_type.assert_called_once_with()
    assert fake.calls == [("secret-1", "v1")]


def test_default_resolver_reuses_backend_scoped_client(isolated_db, monkeypatch):
    import provider_credentials as credentials

    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    fake = FakeSecretManagementClient({("secret-1", "v1"): "cached-client-value"})
    monkeypatch.setattr(credentials, "_SECRET_MANAGEMENT_CLIENT", fake)

    assert (
        resolve_secret_management_value(isolated_db, "alice_short_token") == "cached-client-value"
    )
    assert (
        resolve_secret_management_value(isolated_db, "alice_short_token") == "cached-client-value"
    )
    assert fake.calls == [("secret-1", "v1"), ("secret-1", "v1")]


def test_atomic_upsert_tracks_immediately_previous_version(isolated_db):
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v1")
    set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v2")
    latest = set_secret_management_ref(isolated_db, "alice_short_token", "secret-1", "v3")

    assert latest.pinned_version_id == "v3"
    assert latest.previous_version_id == "v2"


def test_rollback_detects_stale_concurrent_update(isolated_db, monkeypatch):
    import provider_credentials as credentials

    set_secret_management_ref(isolated_db, "alice_database_url", "secret-1", "v1")
    set_secret_management_ref(isolated_db, "alice_database_url", "secret-1", "v2")

    stale = get_secret_management_ref(isolated_db, "alice_database_url")
    original_get = credentials.get_secret_management_ref
    calls = {"count": 0}

    def stale_then_real(db, purpose):
        calls["count"] += 1
        if calls["count"] == 1:
            return stale
        return original_get(db, purpose)

    monkeypatch.setattr(credentials, "get_secret_management_ref", stale_then_real)
    isolated_db.execute(
        "UPDATE secret_management_refs SET pinned_version_id = ?, previous_version_id = ? "
        "WHERE purpose = ?",
        ("v3", "v2", "alice_database_url"),
    )
    isolated_db.commit()

    with pytest.raises(CredentialError, match="changed concurrently"):
        rollback_secret_management_ref(isolated_db, "alice_database_url")
