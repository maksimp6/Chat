import json

from secret_store.admin import SecretAdminBackend
from secret_store.core import SecretValue
from secret_store.fake_admin import FakeSecretAdminBackend


def _backend() -> SecretAdminBackend:
    return FakeSecretAdminBackend()


def test_admin_contract_returns_metadata_reference_not_plaintext():
    backend = _backend()

    result = backend.create(purpose="github", value=SecretValue("canary-admin-secret"))

    serialized = json.dumps(result.ref.metadata())
    assert result.created is True
    assert result.ref.version_id == "v1"
    assert "canary-admin-secret" not in serialized
    assert "canary-admin-secret" not in repr(result)


def test_rotation_keeps_secret_identity_and_advances_version():
    backend = FakeSecretAdminBackend()
    first = backend.create(purpose="github", value=SecretValue("old-token"))
    second = backend.rotate(first.ref, SecretValue("new-token"))

    assert second.created is False
    assert second.ref.secret_id == first.ref.secret_id
    assert second.ref.version_id == "v2"
    assert backend.resolve_for_test(first.ref) == "old-token"
    assert backend.resolve_for_test(second.ref) == "new-token"


def test_delete_removes_all_versions_without_returning_values():
    backend = FakeSecretAdminBackend()
    first = backend.create(purpose="github", value=SecretValue("old-token"))
    second = backend.rotate(first.ref, SecretValue("new-token"))

    result = backend.delete(second.ref)

    assert result is None
    assert backend.resolve_for_test(first.ref) is None
    assert backend.resolve_for_test(second.ref) is None
