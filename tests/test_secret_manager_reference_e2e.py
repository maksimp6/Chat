from secret_store.core import SecretErrorCode, SecretRef, SecretResolutionError, SecretValue
from secret_store.fake_admin import FakeSecretAdminBackend
from secret_store.manager import InMemorySecretAliasStore, SecretManager


class AdminResolver:
    def __init__(self, admin: FakeSecretAdminBackend) -> None:
        self.admin = admin

    def resolve(self, ref: SecretRef) -> SecretValue:
        value = self.admin.resolve_for_test(ref)
        if value is None:
            raise SecretResolutionError(SecretErrorCode.NOT_FOUND, ref)
        return SecretValue(value)


def test_reference_password_manager_full_lifecycle():
    admin = FakeSecretAdminBackend()
    resolver = AdminResolver(admin)
    manager = SecretManager(InMemorySecretAliasStore(), resolver, admin)

    created = manager.create(
        "github",
        SecretValue("canary-v1"),
        secret_purpose="github",
        allowed_purposes=frozenset({"rdc.github", "browser.password"}),
    )

    assert created.ref.version_id == "v1"
    assert manager.use("github", "rdc.github").reveal() == "canary-v1"

    rotated = manager.rotate("github", SecretValue("canary-v2"))

    assert rotated.alias == "github"
    assert rotated.ref.secret_id == created.ref.secret_id
    assert rotated.ref.version_id == "v2"
    assert manager.use("github", "browser.password").reveal() == "canary-v2"

    manager.delete("github")

    assert manager.list_aliases() == ()
    try:
        manager.use("github", "rdc.github")
    except Exception as error:
        assert "canary-v1" not in str(error)
        assert "canary-v2" not in str(error)
    else:
        raise AssertionError("deleted alias must fail closed")

    events = manager.audit_events()
    assert [event.operation for event in events] == [
        "create",
        "use",
        "rotate",
        "use",
        "delete",
        "use",
    ]
    assert "canary-v1" not in repr(events)
    assert "canary-v2" not in repr(events)
