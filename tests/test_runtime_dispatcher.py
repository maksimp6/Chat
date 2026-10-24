from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime import (
    FILESYSTEM_READ_TEXT,
    FILESYSTEM_WRITE_TEXT,
    RuntimeDispatcher,
    RuntimeNotFound,
    RuntimeOperationNotFound,
    RuntimeOwnerViolation,
    RuntimeScopeViolation,
)


def test_runtime_filesystems_are_isolated_during_concurrent_access(tmp_path):
    dispatcher = RuntimeDispatcher()
    root_a = tmp_path / "runtime-a"
    root_b = tmp_path / "runtime-b"
    dispatcher.register_runtime("runtime-a", root=str(root_a))
    dispatcher.register_runtime("runtime-b", root=str(root_b))

    def write_and_read(runtime_id, value):
        dispatcher.dispatch(
            runtime_id,
            FILESYSTEM_WRITE_TEXT,
            {"path": "state/value.txt", "content": value},
        )
        return dispatcher.dispatch(runtime_id, FILESYSTEM_READ_TEXT, {"path": "state/value.txt"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_and_read, "runtime-a", "first")
        second = pool.submit(write_and_read, "runtime-b", "second")

    assert first.result() == "first"
    assert second.result() == "second"
    assert (root_a / "state/value.txt").read_text() == "first"
    assert (root_b / "state/value.txt").read_text() == "second"


@pytest.mark.parametrize("path", ["../runtime-b/secret.txt", "/tmp/secret.txt"])
def test_runtime_filesystem_rejects_paths_outside_root(tmp_path, path):
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", root=str(tmp_path / "runtime-a"))

    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch(
            "runtime-a",
            FILESYSTEM_WRITE_TEXT,
            {"path": path, "content": "not written"},
        )


def test_runtime_filesystem_rejects_symlink_escape(tmp_path):
    runtime_root = tmp_path / "runtime-a"
    outside = tmp_path / "outside"
    runtime_root.mkdir()
    outside.mkdir()
    (runtime_root / "escape").symlink_to(outside, target_is_directory=True)
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", root=str(runtime_root))

    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch(
            "runtime-a",
            FILESYSTEM_WRITE_TEXT,
            {"path": "escape/secret.txt", "content": "not written"},
        )

    assert not (outside / "secret.txt").exists()


def test_runtime_filesystem_never_falls_back_to_process_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-without-root")

    with pytest.raises(RuntimeScopeViolation, match="no filesystem root"):
        dispatcher.dispatch(
            "runtime-without-root",
            FILESYSTEM_WRITE_TEXT,
            {"path": "global.txt", "content": "not written"},
        )

    assert not (tmp_path / "global.txt").exists()


def test_dispatcher_executes_operation_in_own_runtime_scope():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a", namespace="env_a")

    seen = {}

    def handler(context, payload):
        seen["context"] = context
        seen["current"] = dispatcher.current_context()
        return {"value": payload["value"]}

    dispatcher.register_operation("echo", handler)

    result = dispatcher.dispatch("runtime-a", "echo", {"value": 42})

    assert result == {"value": 42}
    assert seen["context"].runtime_id == "runtime-a"
    assert seen["context"].namespace == "env_a"
    assert seen["current"] == seen["context"]


def test_dispatcher_rejects_cross_runtime_resource_access():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_runtime("runtime-b")
    dispatcher.register_operation("read", lambda context, payload: payload)

    with pytest.raises(RuntimeScopeViolation):
        dispatcher.dispatch(
            "runtime-a",
            "read",
            {},
            resource_runtime_id="runtime-b",
        )


def test_dispatcher_rejects_authenticated_owner_mismatch():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="owner-a")
    dispatcher.register_operation("read", lambda context, payload: payload)

    with pytest.raises(RuntimeScopeViolation, match="owner"):
        dispatcher.dispatch("runtime-a", "read", caller_owner_id="owner-b")


def test_dispatcher_rejects_unknown_runtime():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_operation("read", lambda context, payload: payload)

    with pytest.raises(RuntimeNotFound):
        dispatcher.dispatch("missing", "read")


def test_dispatcher_rejects_unknown_operation():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")

    with pytest.raises(RuntimeOperationNotFound):
        dispatcher.dispatch("runtime-a", "missing")


def test_dispatcher_validates_registration_inputs():
    dispatcher = RuntimeDispatcher()

    with pytest.raises(ValueError):
        dispatcher.register_runtime("")
    with pytest.raises(ValueError):
        dispatcher.register_operation("", lambda context, payload: payload)
    with pytest.raises(TypeError):
        dispatcher.register_operation("bad", object())


def test_unregister_runtime_removes_scope():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.unregister_runtime("runtime-a")

    with pytest.raises(RuntimeNotFound):
        dispatcher.context("runtime-a")


def test_current_context_is_cleared_after_dispatch():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_operation("noop", lambda context, payload: None)

    dispatcher.dispatch("runtime-a", "noop")

    with pytest.raises(RuntimeNotFound):
        dispatcher.current_context()


def test_nested_dispatch_restores_outer_runtime_context():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")
    dispatcher.register_operation(
        "inner",
        lambda context, payload: dispatcher.current_context().runtime_id,
    )

    def outer(context, payload):
        inner = dispatcher.dispatch("runtime-a", "inner")
        return inner, dispatcher.current_context().runtime_id

    dispatcher.register_operation("outer", outer)

    assert dispatcher.dispatch("runtime-a", "outer") == ("runtime-a", "runtime-a")


def test_dispatch_cleanup_tolerates_handler_clearing_thread_binding():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a")

    def handler(context, payload):
        del dispatcher._local.runtime_id
        return context.runtime_id

    dispatcher.register_operation("clear-binding", handler)

    assert dispatcher.dispatch("runtime-a", "clear-binding") == "runtime-a"

    with pytest.raises(RuntimeNotFound):
        dispatcher.current_context()


def test_runtime_authorization_hides_cross_owner_scope():
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("runtime-a", owner_id="alice")
    dispatcher.register_runtime("runtime-b", owner_id="bob")

    assert dispatcher.authorize("runtime-a", "alice").runtime_id == "runtime-a"
    assert dispatcher.authorize("runtime-a", None).runtime_id == "runtime-a"

    with pytest.raises(RuntimeOwnerViolation):
        dispatcher.authorize("runtime-b", "alice")

    with pytest.raises(RuntimeNotFound):
        dispatcher.authorize("missing", "alice")
