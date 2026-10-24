import json

import pytest

from secret_store.manager import (
    InMemorySecretAliasStore,
    SecretAlias,
    SecretAliasError,
    SecretManager,
)
from secret_store.core import SecretValue
from secret_store.core import SecretRef
from secret_store.fake import FakeSecretResolver
from secret_store.fake_admin import FakeSecretAdminBackend


def _ref(version="v1"):
    return SecretRef(
        provider="fake",
        secret_id="github-token",
        version_id=version,
        purpose="github",
    )


def _entry(ref=None):
    return SecretAlias(
        alias="github",
        ref=ref or _ref(),
        allowed_purposes=frozenset({"rdc.github", "github.api"}),
    )


def test_alias_registry_lists_metadata_without_plaintext():
    ref = _ref()
    store = InMemorySecretAliasStore()
    manager = SecretManager(store, FakeSecretResolver({ref: "canary-secret"}))
    manager.put_alias(_entry(ref))

    entries = manager.list_aliases()

    assert entries == (_entry(ref),)
    serialized = json.dumps(
        {
            "alias": entries[0].alias,
            "ref": entries[0].ref.metadata(),
            "purposes": sorted(entries[0].allowed_purposes),
        }
    )
    assert "canary-secret" not in serialized


def test_authorized_runtime_can_resolve_alias():
    ref = _ref()
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver({ref: "canary-secret"}),
    )
    manager.put_alias(_entry(ref))

    value = manager.use("github", "rdc.github")

    assert value.reveal() == "canary-secret"
    event = manager.audit_events()[-1]
    assert (event.alias, event.purpose, event.operation, event.success) == (
        "github",
        "rdc.github",
        "use",
        True,
    )
    assert "canary-secret" not in repr(event)


def test_disallowed_purpose_fails_before_resolver():
    ref = _ref()
    resolver = FakeSecretResolver({ref: "canary-secret"})
    manager = SecretManager(InMemorySecretAliasStore(), resolver)
    manager.put_alias(_entry(ref))

    with pytest.raises(SecretAliasError, match="not allowed"):
        manager.use("github", "browser.password")

    assert manager.audit_events()[-1].success is False


def test_missing_alias_is_safe_and_audited():
    manager = SecretManager(InMemorySecretAliasStore(), FakeSecretResolver())

    with pytest.raises(SecretAliasError, match="not found") as error:
        manager.use("missing", "rdc.github")

    assert "github-token" not in str(error.value)
    assert manager.audit_events()[-1].success is False


def test_rotation_changes_reference_without_changing_alias_or_consumer():
    v1 = _ref("v1")
    v2 = _ref("v2")
    resolver = FakeSecretResolver({v1: "old-token", v2: "new-token"})
    manager = SecretManager(InMemorySecretAliasStore(), resolver)
    manager.put_alias(_entry(v1))

    assert manager.use("github", "rdc.github").reveal() == "old-token"

    manager.put_alias(_entry(v2))

    assert manager.use("github", "rdc.github").reveal() == "new-token"


def test_delete_removes_alias_access():
    ref = _ref()
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver({ref: "canary-secret"}),
    )
    manager.put_alias(_entry(ref))
    manager.delete_alias("github")

    with pytest.raises(SecretAliasError, match="not found"):
        manager.use("github", "rdc.github")


@pytest.mark.parametrize("alias", ["", "GITHUB", "a", "bad alias", "../token"])
def test_alias_validation(alias):
    with pytest.raises(ValueError, match="invalid secret alias"):
        SecretAlias(alias=alias, ref=_ref(), allowed_purposes=frozenset({"rdc.github"}))


def test_alias_requires_acl():
    with pytest.raises(ValueError, match="allowed purpose"):
        SecretAlias(alias="github", ref=_ref(), allowed_purposes=frozenset())


def test_resolver_failure_is_audited_without_secret_material():
    ref = _ref()
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver({ref: "canary-secret"}, available=False),
    )
    manager.put_alias(_entry(ref))

    with pytest.raises(Exception) as error:
        manager.use("github", "rdc.github")

    event = manager.audit_events()[-1]
    assert event.success is False
    assert "canary-secret" not in repr(event)
    assert "canary-secret" not in str(error.value)


def test_admin_operations_fail_closed_without_admin_backend():
    ref = _ref()
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver({ref: "canary-secret"}),
    )
    manager.put_alias(_entry(ref))

    with pytest.raises(SecretAliasError, match="admin backend"):
        manager.rotate("github", SecretValue("new-secret"))


def test_rotate_missing_alias_fails_closed():
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver(),
        FakeSecretAdminBackend(),
    )

    with pytest.raises(SecretAliasError, match="not found"):
        manager.rotate("missing", SecretValue("new-secret"))


def test_delete_missing_alias_fails_closed():
    manager = SecretManager(
        InMemorySecretAliasStore(),
        FakeSecretResolver(),
        FakeSecretAdminBackend(),
    )

    with pytest.raises(SecretAliasError, match="not found"):
        manager.delete("missing")


def test_create_or_rotate_creates_then_rotates_without_changing_policy():
    admin = FakeSecretAdminBackend()
    manager = SecretManager(InMemorySecretAliasStore(), FakeSecretResolver(), admin)

    first = manager.create_or_rotate(
        "github",
        SecretValue("old-secret"),
        secret_purpose="github",
        allowed_purposes=frozenset({"browser.password"}),
    )
    second = manager.create_or_rotate(
        "github",
        SecretValue("new-secret"),
        secret_purpose="github",
        allowed_purposes=frozenset({"browser.password"}),
    )

    assert first.alias == second.alias == "github"
    assert first.ref.secret_id == second.ref.secret_id
    assert first.ref.version_id == "v1"
    assert second.ref.version_id == "v2"
    assert second.allowed_purposes == frozenset({"browser.password"})
    assert admin.resolve_for_test(first.ref) == "old-secret"
    assert admin.resolve_for_test(second.ref) == "new-secret"


def test_create_or_rotate_rejects_secret_purpose_drift():
    admin = FakeSecretAdminBackend()
    manager = SecretManager(InMemorySecretAliasStore(), FakeSecretResolver(), admin)
    manager.create_or_rotate(
        "github",
        SecretValue("old-secret"),
        secret_purpose="github",
        allowed_purposes=frozenset({"browser.password"}),
    )

    with pytest.raises(SecretAliasError, match="policy mismatch"):
        manager.create_or_rotate(
            "github",
            SecretValue("new-secret"),
            secret_purpose="ssh",
            allowed_purposes=frozenset({"browser.password"}),
        )


def test_create_or_rotate_rejects_acl_drift():
    admin = FakeSecretAdminBackend()
    manager = SecretManager(InMemorySecretAliasStore(), FakeSecretResolver(), admin)
    manager.create_or_rotate(
        "github",
        SecretValue("old-secret"),
        secret_purpose="github",
        allowed_purposes=frozenset({"browser.password"}),
    )

    with pytest.raises(SecretAliasError, match="policy mismatch"):
        manager.create_or_rotate(
            "github",
            SecretValue("new-secret"),
            secret_purpose="github",
            allowed_purposes=frozenset({"browser.password", "rdc.git"}),
        )
