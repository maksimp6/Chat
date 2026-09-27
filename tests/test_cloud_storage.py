from pathlib import Path

import pytest

from cloud_storage import (
    LocalDirectoryStorage,
    ScopedStorageRegistry,
    StorageCredentialsMissing,
    StorageProviderError,
    StorageProviderNotConfigured,
    install_storage_operation,
)
from runtime import RuntimeDispatcher, RuntimeScopeViolation, RuntimeStorage
from trace_manager import ExecutionTrace


def configured_storage(tmp_path: Path, *, trace=None):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="alice")
    dispatcher.register_runtime("runtime-b", owner_id="bob")
    registry = ScopedStorageRegistry()
    registry.register("runtime-a", "alice", "local", lambda context: LocalDirectoryStorage(tmp_path / context.namespace))
    install_storage_operation(dispatcher, registry)
    return RuntimeStorage(dispatcher, "runtime-a", trace), registry


def test_local_upload_download_and_list_are_runtime_scoped(tmp_path):
    storage, _ = configured_storage(tmp_path)

    uploaded = storage.upload("local", "reports/result.txt", b"hello")

    assert uploaded == {
        "object_id": "reports/result.txt", "name": "result.txt", "size": 5,
        "provider": "local", "location": "local:reports/result.txt",
    }
    assert storage.download("local", uploaded["object_id"]) == b"hello"
    assert storage.list("local", "reports") == [uploaded]


def test_cross_runtime_storage_access_is_rejected_before_provider_call(tmp_path):
    storage, _ = configured_storage(tmp_path)
    with pytest.raises(RuntimeScopeViolation):
        storage.list("local", resource_runtime_id="runtime-b")


def test_provider_configuration_is_owner_scoped(tmp_path):
    storage, _ = configured_storage(tmp_path)
    with pytest.raises(StorageProviderNotConfigured, match="not configured"):
        storage.list("drive")


def test_cloud_adapter_credentials_are_resolved_only_inside_registry(tmp_path):
    class CloudAdapter:
        requires_credentials = True

        def list(self, prefix="", credentials=None):
            raise AssertionError("adapter must not run without credentials")

    storage, registry = configured_storage(tmp_path)
    registry.register("runtime-a", "alice", "drive", lambda context: CloudAdapter())
    with pytest.raises(StorageCredentialsMissing, match="credentials are missing"):
        storage.list("drive")


def test_provider_failure_is_sanitized_in_error_and_trace(tmp_path):
    class BrokenAdapter:
        requires_credentials = False

        def list(self, prefix="", credentials=None):
            raise RuntimeError("token=provider-secret")

    trace = ExecutionTrace()
    storage, registry = configured_storage(tmp_path, trace=trace)
    registry.register("runtime-a", "alice", "broken", lambda context: BrokenAdapter())

    with pytest.raises(StorageProviderError, match="operation failed") as error:
        storage.list("broken")
    assert "provider-secret" not in str(error.value)
    assert "provider-secret" not in str(trace.finalize())


def test_trace_exposes_operation_but_not_content_or_credentials(tmp_path):
    trace = ExecutionTrace()
    storage, _ = configured_storage(tmp_path, trace=trace)
    storage.upload("local", "artifact.txt", b"TOP-SECRET-CONTENT")

    events = [event for event in trace.finalize()["events"] if event["type"].startswith("storage_")]
    assert [event["type"] for event in events] == ["storage_operation_started", "storage_operation_completed"]
    serialized = str(events)
    assert "artifact.txt" not in serialized
    assert "TOP-SECRET" not in serialized


@pytest.mark.parametrize("name", ["../secret", "/absolute", "folder/../secret"])
def test_local_adapter_rejects_path_escape(tmp_path, name):
    provider = LocalDirectoryStorage(tmp_path)
    with pytest.raises(Exception, match="invalid storage object name"):
        provider.upload(name, b"data")
