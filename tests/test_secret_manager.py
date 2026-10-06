import json

import pytest

from secret_manager import (
    InMemorySecretAliasStore,
    SecretAlias,
    SecretAliasError,
    SecretManager,
)
from secret_store.core import SecretRef
from secret_store.fake import FakeSecretResolver


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
