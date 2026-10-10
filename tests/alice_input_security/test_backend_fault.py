"""Root dispatch fault must revoke the session and keep socket fail-closed."""
import os
import pytest
from alice_mouse.security import InputGrant, GrantError
from test_socket_boundary import harness, send


def test_backend_exception_revokes_and_denies_followup(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    def broken(action, payload):
        raise OSError('injected uinput failure')
    verifier.dispatch = broken
    endpoint.start()
    first = signer.sign(principal, InputGrant('key_down', {'key':30}))
    second = signer.sign(principal, InputGrant('key_up', {'key':30}))
    assert send(endpoint.path, first) == b'DENIED\n'
    assert send(endpoint.path, second) == b'DENIED\n'
    assert verifier.session is None
    assert endpoint._thread.is_alive()


def test_backend_false_result_revokes_session(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    verifier.dispatch = lambda action, payload: False
    endpoint.start()
    packet = signer.sign(principal, InputGrant('move', {'x':1,'y':1}))
    assert send(endpoint.path, packet) == b'DENIED\n'
    assert verifier.session is None
    assert endpoint._thread.is_alive()
