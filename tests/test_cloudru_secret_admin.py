import base64

import pytest

from cloud.base import CloudProviderError
from cloud.cloudru.secret_management_admin import CloudRuSecretManagementAdminClient
from secret_store.cloudru_admin import CloudRuSecretAdminBackend
from secret_store.core import SecretRef, SecretValue


class FakeIam:
    key_id = "admin-key"
    key_secret = "admin-secret"

    def _token(self):
        return "token"


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload
        self.content = b"" if payload is None else b"{}"

    def json(self):
        return self._payload


def client():
    result = CloudRuSecretManagementAdminClient(
        iam_client=FakeIam(),
        parent_id="parent-1",
    )
    result._client.endpoint = lambda _service: "https://secretmanager.api.cloud.ru"
    return result


def test_admin_client_create_encodes_secret_without_returning_plaintext(monkeypatch):
    seen = {}

    def request(method, url, **kwargs):
        seen.update(method=method, url=url, json=kwargs["json"])
        return Response(payload={"id": "secret-1", "version": {"id": "version-1"}})

    monkeypatch.setattr("requests.request", request)
    secret_id, version_id = client().create_secret(
        name="alice-github",
        value="synthetic-secret",
    )

    assert (secret_id, version_id) == ("secret-1", "version-1")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v1/secrets")
    encoded = seen["json"]["payload"]["data"]["value"]
    assert base64.b64decode(encoded).decode() == "synthetic-secret"
    assert "synthetic-secret" not in repr((secret_id, version_id))


def test_admin_client_create_version_uses_version_collection(monkeypatch):
    seen = {}

    def request(method, url, **kwargs):
        seen.update(method=method, url=url, json=kwargs["json"])
        return Response(payload={"version_id": "version-2"})

    monkeypatch.setattr("requests.request", request)
    version_id = client().create_version(
        secret_id="secret-1",
        value="synthetic-secret",
    )

    assert version_id == "version-2"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v1/secrets/secret-1/versions")


def test_admin_client_delete_has_no_secret_payload(monkeypatch):
    seen = {}

    def request(method, url, **kwargs):
        seen.update(method=method, url=url, json=kwargs["json"])
        return Response(status=204)

    monkeypatch.setattr("requests.request", request)
    client().delete_secret("secret-1")

    assert seen == {
        "method": "DELETE",
        "url": "https://secretmanager.api.cloud.ru/v1/secrets/secret-1",
        "json": None,
    }


def test_admin_client_provider_error_never_contains_secret(monkeypatch):
    monkeypatch.setattr(
        "requests.request",
        lambda *args, **kwargs: Response(status=500, payload={"detail": "synthetic-secret"}),
    )

    with pytest.raises(CloudProviderError) as error:
        client().create_version(secret_id="secret-1", value="synthetic-secret")

    assert "synthetic-secret" not in str(error.value)


class FakeAdminClient:
    def __init__(self):
        self.created = []
        self.rotated = []
        self.deleted = []

    def create_secret(self, *, name, value, description):
        self.created.append((name, value, description))
        return "secret-1", "v1"

    def create_version(self, *, secret_id, value):
        self.rotated.append((secret_id, value))
        return "v2"

    def delete_secret(self, secret_id):
        self.deleted.append(secret_id)


def test_cloudru_admin_backend_create_rotate_delete():
    low = FakeAdminClient()
    backend = CloudRuSecretAdminBackend(low)

    first = backend.create(purpose="github", value=SecretValue("old-secret"))
    second = backend.rotate(first.ref, SecretValue("new-secret"))
    backend.delete(second.ref)

    assert first.created is True
    assert first.ref.provider == "cloudru"
    assert first.ref.version_id == "v1"
    assert second.created is False
    assert second.ref.secret_id == first.ref.secret_id
    assert second.ref.version_id == "v2"
    assert low.rotated == [("secret-1", "new-secret")]
    assert low.deleted == ["secret-1"]


def test_cloudru_admin_backend_rejects_non_cloudru_reference():
    backend = CloudRuSecretAdminBackend(FakeAdminClient())
    ref = SecretRef(provider="fake", secret_id="s", version_id="v1", purpose="github")

    with pytest.raises(ValueError, match="cloudru"):
        backend.rotate(ref, SecretValue("new-secret"))
