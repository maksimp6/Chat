import json

import pytest

from secret_store.core import SecretRef
from secret_store.delivery import deliver_secret
from secret_store.fake import FakeSecretResolver
from secret_store.manager import InMemorySecretAliasStore, SecretAlias, SecretManager


def _manager():
    ref = SecretRef(provider="fake", secret_id="s1", version_id="v1", purpose="github")
    store = InMemorySecretAliasStore()
    store.put(SecretAlias("github", ref, frozenset({"browser.password", "rdc.git"})))
    return SecretManager(store, FakeSecretResolver({ref: "synthetic-secret"}))


def test_agent_delivery_returns_metadata_not_plaintext():
    captured = []
    result = deliver_secret(
        _manager(),
        alias="github",
        purpose="browser.password",
        operation="browser.fill",
        consumer=lambda value: captured.append(value.reveal()),
    )

    assert captured == ["synthetic-secret"]
    assert "synthetic-secret" not in repr(result)
    assert "synthetic-secret" not in json.dumps(
        result.__dict__
        if hasattr(result, "__dict__")
        else {
            "alias": result.alias,
            "purpose": result.purpose,
            "operation": result.operation,
            "ok": result.ok,
        }
    )


def test_delivery_enforces_manager_purpose_acl_before_consumer():
    called = False

    def consume(_value):
        nonlocal called
        called = True

    with pytest.raises(Exception, match="purpose"):
        deliver_secret(
            _manager(),
            alias="github",
            purpose="browser.cookie",
            operation="browser.fill",
            consumer=consume,
        )
    assert called is False


def test_delivery_has_no_reveal_operation():
    with pytest.raises(ValueError, match="unsupported"):
        deliver_secret(
            _manager(),
            alias="github",
            purpose="browser.password",
            operation="reveal",
            consumer=lambda _value: None,
        )
