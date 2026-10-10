"""Isolated signed socket -> verifier -> keyboard-like backend contract.

No uinput, root process, or Android event injection occurs here.
"""
import secrets
from alice_mouse.security import InputGrant, GrantError
from test_socket_boundary import harness,send


def test_signed_keyboard_chord_and_release(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    held=set()
    def keyboard(action,payload):
        key=payload['key']
        if action=='key_down':
            if key in held:return False
            held.add(key)
        elif action=='key_up':
            if key not in held:return False
            held.remove(key)
        else:return False
        calls.append((action,key))
        return True
    verifier.dispatch=keyboard
    endpoint.start()
    for action,key in [('key_down',29),('key_down',30),('key_up',30),('key_up',29)]:
        packet=signer.sign(principal,InputGrant(action,{'key':key}))
        assert send(endpoint.path,packet)==b'OK\n'
    assert calls==[('key_down',29),('key_down',30),('key_up',30),('key_up',29)]
    assert not held


def test_unsigned_key_replay_and_forbidden_power_never_reach_backend(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    endpoint.start()
    assert send(endpoint.path,b'key_down 30')==b'DENIED\n'
    packet=signer.sign(principal,InputGrant('key_down',{'key':30}))
    assert send(endpoint.path,packet)==b'OK\n'
    assert send(endpoint.path,packet)==b'DENIED\n'
    power=signer.sign(principal,InputGrant('key_down',{'key':116}))
    assert send(endpoint.path,power)==b'DENIED\n'
    assert calls==[('key_down',{'key':30})]


def test_keyboard_backend_fault_revokes_followup(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    held=set()
    def failing(action,payload):
        held.add(payload['key'])
        raise OSError('partial event write')
    verifier.dispatch=failing
    endpoint.start()
    first=signer.sign(principal,InputGrant('key_down',{'key':30}))
    assert send(endpoint.path,first)==b'DENIED\n'
    assert verifier.session is None
    second=signer.sign(principal,InputGrant('key_up',{'key':30}))
    assert send(endpoint.path,second)==b'DENIED\n'
    assert held=={30}  # shows why independent C watchdog is mandatory
