from __future__ import annotations

from pathlib import Path

import pytest

from runtime import RuntimeDispatcher, RuntimeScopeViolation, RuntimeStorage
from storage import (
    LocalDirectoryStorage,
    StorageCredentialsMissing,
    StorageError,
    StorageProviderUnavailable,
)


def configured_storage(tmp_path: Path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a")
    dispatcher.register_storage_provider("runtime-a", LocalDirectoryStorage(tmp_path))
    return dispatcher, RuntimeStorage(dispatcher, "runtime-a")


def test_local_provider_upload_download_and_list(tmp_path):
    _, storage = configured_storage(tmp_path)

    saved = storage.upload("local", "reports/result.txt", b"result")

    assert saved.object_id == "reports/result.txt"
    assert saved.location == "local:reports/result.txt"
    assert storage.download("local", saved.object_id) == b"result"
    assert [item.object_id for item in storage.list("local", "reports")] == ["reports/result.txt"]


def test_provider_registration_is_runtime_and_owner_scoped(tmp_path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a")
    dispatcher.register_runtime("runtime-b", owner_id="owner-b")
    dispatcher.register_storage_provider("runtime-a", LocalDirectoryStorage(tmp_path))

    with pytest.raises(StorageProviderUnavailable):
        RuntimeStorage(dispatcher, "runtime-b").list("local")
    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch_storage("runtime-a", "local", "list", resource_runtime_id="runtime-b")
    with pytest.raises(RuntimeScopeViolation):
        dispatcher.register_storage_provider(
            "runtime-a", LocalDirectoryStorage(tmp_path / "other"), owner_id="owner-b"
        )


class CredentialProvider:
    name = "cloud"
    requires_credentials = True

    def upload(self, object_id, content, *, credentials):
        return credentials

    def download(self, object_id, *, credentials):
        return b""

    def list(self, prefix, *, credentials):
        return []


def test_credentials_are_resolved_inside_dispatcher(tmp_path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a")
    dispatcher.register_storage_provider("runtime-a", CredentialProvider())

    with pytest.raises(StorageCredentialsMissing):
        RuntimeStorage(dispatcher, "runtime-a").list("cloud")

    secret = object()
    dispatcher.set_storage_credential_resolver(lambda context, provider: secret)
    assert RuntimeStorage(dispatcher, "runtime-a").upload("cloud", "item", b"data") is secret


def test_provider_errors_are_sanitized(tmp_path):
    class BrokenProvider(CredentialProvider):
        requires_credentials = False

        def list(self, prefix, *, credentials):
            raise RuntimeError("token=do-not-leak")

    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_storage_provider("runtime-a", BrokenProvider())

    with pytest.raises(StorageError, match="Storage provider operation failed") as caught:
        RuntimeStorage(dispatcher, "runtime-a").list("cloud")
    assert "do-not-leak" not in str(caught.value)


def test_trace_has_lifecycle_but_no_object_data_or_credentials(tmp_path):
    dispatcher, _ = configured_storage(tmp_path)
    events = []
    storage = RuntimeStorage(
        dispatcher, "runtime-a", trace=lambda kind, data: events.append((kind, data))
    )

    storage.upload("local", "private/name.txt", b"secret-content")

    assert [kind for kind, _ in events] == ["storage.started", "storage.completed"]
    assert events[0][1] == {
        "action": "upload",
        "provider": "local",
        "runtime_id": "runtime-a",
    }
    assert "private" not in repr(events)
    assert "secret-content" not in repr(events)


@pytest.mark.parametrize(
    "name", ["../secret", "folder/../../secret", "/absolute", "folder\\..\\secret"]
)
def test_local_provider_rejects_path_traversal(tmp_path, name):
    _, storage = configured_storage(tmp_path)
    with pytest.raises(StorageError, match="Invalid storage object name"):
        storage.upload("local", name, b"nope")
