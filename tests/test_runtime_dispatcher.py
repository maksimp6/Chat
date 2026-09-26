import pytest

from runtime import (
    RuntimeDispatcher,
    RuntimeNotFound,
    RuntimeOperationNotFound,
    RuntimeScopeViolation,
)


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
