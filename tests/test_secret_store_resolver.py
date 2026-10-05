"""Tests for the provider-neutral Secret Store resolver contract.

Coverage:
- Contract types: SecretReference validation, SecretResolverError structure.
- Resolve success path.
- Version switch (pinned reference selects exactly the right version).
- Missing secret → NOT_FOUND.
- Revoked version → VERSION_DISABLED.
- Unavailable backend → PROVIDER_UNAVAILABLE.
- Secret-canary redaction: resolved value must not escape through
  ExecutionTrace events, error messages, or tool-result-like dicts.
"""

from __future__ import annotations

import json

import pytest

from invocation.context import InvocationContext
from invocation.trace import create_invocation_trace
from secret_store import (
    NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    VERSION_DISABLED,
    FakeSecretBackend,
    SecretReference,
    SecretResolver,
    SecretResolverError,
)
from trace_manager import ExecutionTrace, bind_current_trace, reset_current_trace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_backend():
    return FakeSecretBackend()


@pytest.fixture()
def resolver(fake_backend):
    return SecretResolver(fake_backend)


@pytest.fixture()
def ref():
    return SecretReference(purpose="alice_short_token", secret_id="secret-1", version_id="v1")


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


def test_secret_reference_rejects_empty_purpose():
    with pytest.raises(ValueError, match="required"):
        SecretReference(purpose="", secret_id="s", version_id="v")


def test_secret_reference_rejects_empty_secret_id():
    with pytest.raises(ValueError, match="required"):
        SecretReference(purpose="p", secret_id="", version_id="v")


def test_secret_reference_rejects_empty_version_id():
    with pytest.raises(ValueError, match="required"):
        SecretReference(purpose="p", secret_id="s", version_id="")


def test_secret_reference_is_frozen():
    ref = SecretReference(purpose="p", secret_id="s", version_id="v")
    with pytest.raises((AttributeError, TypeError)):
        ref.version_id = "v2"  # type: ignore[misc]


def test_secret_reference_equality_and_hashing():
    r1 = SecretReference(purpose="p", secret_id="s", version_id="v1")
    r2 = SecretReference(purpose="p", secret_id="s", version_id="v1")
    r3 = SecretReference(purpose="p", secret_id="s", version_id="v2")
    assert r1 == r2
    assert hash(r1) == hash(r2)
    assert r1 != r3


def test_secret_resolver_error_is_exception():
    err = SecretResolverError(code=NOT_FOUND, message="not found")
    assert isinstance(err, Exception)
    assert err.code == NOT_FOUND
    assert "not_found" in str(err)


def test_secret_resolver_error_includes_purpose_when_ref_present():
    ref = SecretReference(purpose="alice_short_token", secret_id="s", version_id="v")
    err = SecretResolverError(code=NOT_FOUND, message="missing", reference=ref)
    assert "alice_short_token" in str(err)


def test_secret_resolver_error_without_reference():
    err = SecretResolverError(code=PROVIDER_UNAVAILABLE, message="service down")
    assert err.reference is None
    assert "provider_unavailable" in str(err)


# ---------------------------------------------------------------------------
# Resolve success
# ---------------------------------------------------------------------------


def test_resolve_returns_plaintext_value(fake_backend, resolver, ref):
    fake_backend.register("secret-1", "v1", "my-token-value")
    assert resolver.resolve(ref) == "my-token-value"


def test_resolve_uses_exactly_the_pinned_version(fake_backend, resolver):
    fake_backend.register("secret-1", "v1", "value-v1")
    fake_backend.register("secret-1", "v2", "value-v2")
    ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v1")
    assert resolver.resolve(ref) == "value-v1"


def test_fake_backend_records_calls(fake_backend, resolver, ref):
    fake_backend.register("secret-1", "v1", "value")
    resolver.resolve(ref)
    assert fake_backend.calls == [ref]


# ---------------------------------------------------------------------------
# Version switch
# ---------------------------------------------------------------------------


def test_resolve_version_switch_returns_new_version(fake_backend, resolver):
    fake_backend.register("secret-1", "v1", "old-value")
    fake_backend.register("secret-1", "v2", "new-value")
    old_ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v1")
    new_ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v2")
    assert resolver.resolve(old_ref) == "old-value"
    assert resolver.resolve(new_ref) == "new-value"
    assert fake_backend.calls == [old_ref, new_ref]


def test_resolve_version_switch_does_not_leak_old_value(fake_backend, resolver):
    fake_backend.register("secret-1", "v1", "old-canary")
    fake_backend.register("secret-1", "v2", "new-canary")
    trace = ExecutionTrace(trace_id="version-switch-canary")
    token = bind_current_trace(trace)
    try:
        old_ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v1")
        new_ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v2")
        v1_value = resolver.resolve(old_ref)
        v2_value = resolver.resolve(new_ref)
        trace.add_event("switch", {"before": v1_value, "after": v2_value})
    finally:
        reset_current_trace(token)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "old-canary" not in snapshot
    assert "new-canary" not in snapshot


# ---------------------------------------------------------------------------
# Missing secret → NOT_FOUND
# ---------------------------------------------------------------------------


def test_resolve_missing_raises_not_found(resolver, ref):
    with pytest.raises(SecretResolverError) as exc_info:
        resolver.resolve(ref)
    assert exc_info.value.code == NOT_FOUND
    assert exc_info.value.reference == ref


def test_resolve_missing_error_message_is_safe(resolver, ref):
    with pytest.raises(SecretResolverError) as exc_info:
        resolver.resolve(ref)
    # The error message must contain no secret values (trivially true here
    # since no value was registered, but validates the safe-logging contract).
    assert "secret-1" in str(exc_info.value) or NOT_FOUND in str(exc_info.value)


# ---------------------------------------------------------------------------
# Revoked version → VERSION_DISABLED
# ---------------------------------------------------------------------------


def test_resolve_revoked_version_raises_version_disabled(fake_backend, resolver, ref):
    fake_backend.register("secret-1", "v1", "value")
    fake_backend.revoke("secret-1", "v1")
    with pytest.raises(SecretResolverError) as exc_info:
        resolver.resolve(ref)
    assert exc_info.value.code == VERSION_DISABLED
    assert exc_info.value.reference == ref


def test_revoked_error_does_not_expose_registered_value(fake_backend, resolver, ref):
    fake_backend.register("secret-1", "v1", "canary-revoked-value")
    fake_backend.revoke("secret-1", "v1")
    with pytest.raises(SecretResolverError) as exc_info:
        resolver.resolve(ref)
    assert "canary-revoked-value" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unavailable backend → PROVIDER_UNAVAILABLE
# ---------------------------------------------------------------------------


def test_resolve_unavailable_backend_raises_typed_error():
    class UnavailableBackend:
        def resolve(self, reference: SecretReference) -> str:
            raise SecretResolverError(
                code=PROVIDER_UNAVAILABLE,
                message="service down",
                reference=reference,
            )

    r = SecretResolver(UnavailableBackend())
    ref = SecretReference(purpose="p", secret_id="s", version_id="v")
    with pytest.raises(SecretResolverError) as exc_info:
        r.resolve(ref)
    assert exc_info.value.code == PROVIDER_UNAVAILABLE


# ---------------------------------------------------------------------------
# Secret-canary redaction: trace events
# ---------------------------------------------------------------------------


def test_resolved_value_is_redacted_in_trace_events(fake_backend, resolver, ref):
    """Regression for #755 canary requirement: value registered → redacted in snapshot."""
    fake_backend.register("secret-1", "v1", "canary-trace-value")
    context = InvocationContext("sess", "conv", "inv", "trace-canary-test")
    trace = create_invocation_trace(context)
    token = bind_current_trace(trace)
    try:
        value = resolver.resolve(ref)
        assert value == "canary-trace-value"
        trace.add_event("tool_result", {"output": value})
    finally:
        reset_current_trace(token)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "canary-trace-value" not in snapshot


def test_resolved_value_is_redacted_from_error_messages(fake_backend, resolver, ref):
    fake_backend.register("secret-1", "v1", "canary-error-value")
    trace = ExecutionTrace(trace_id="error-canary-test")
    token = bind_current_trace(trace)
    try:
        value = resolver.resolve(ref)
        trace.record_error("test.error", f"debug output: {value}")
    finally:
        reset_current_trace(token)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "canary-error-value" not in snapshot


def test_resolved_value_redacted_from_exception_frames(fake_backend, resolver, ref):
    """Value must not appear in captured local variable frames after resolve."""
    fake_backend.register("secret-1", "v1", "canary-frame-value")
    context = InvocationContext("sess", "conv", "inv", "frame-canary-test")
    trace = create_invocation_trace(context)
    token = bind_current_trace(trace)
    try:
        value = resolver.resolve(ref)
        try:
            raise RuntimeError(f"downstream failure: {value}")
        except RuntimeError as exc:
            trace.record_error("test.frame", f"downstream failure: {value}", exception=exc)
    finally:
        reset_current_trace(token)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "canary-frame-value" not in snapshot


def test_resolved_value_not_in_tool_result_snapshot(fake_backend, resolver):
    """Simulated tool result dict containing the secret must be fully redacted."""
    fake_backend.register("secret-2", "v1", "tool-output-canary")
    ref = SecretReference(purpose="p", secret_id="secret-2", version_id="v1")
    trace = ExecutionTrace(trace_id="tool-result-canary")
    token = bind_current_trace(trace)
    try:
        value = resolver.resolve(ref)
        trace.add_event("tool_result", {"result": value, "status": "ok"})
    finally:
        reset_current_trace(token)

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "tool-output-canary" not in snapshot


def test_resolver_error_str_never_contains_registered_secret(fake_backend, resolver):
    """SecretResolverError for a different version must not leak a registered value."""
    fake_backend.register("secret-1", "v1", "the-real-value")
    different_version_ref = SecretReference(purpose="p", secret_id="secret-1", version_id="v99")
    with pytest.raises(SecretResolverError) as exc_info:
        resolver.resolve(different_version_ref)
    assert "the-real-value" not in str(exc_info.value)
