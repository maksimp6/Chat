import os
import secrets
import socket
import tempfile
from pathlib import Path
import pytest

from alice_mouse.security import AuthenticatedPrincipal,SessionAuthority,TrustedSigner,InputGrant,ProtectedVerifier,GrantError
from alice_mouse.security.socket_boundary import SignedInputSocket,SocketBoundaryError


@pytest.fixture
def harness():
    with tempfile.TemporaryDirectory(prefix="alice-input-socket-") as d:
        directory=Path(d)
        os.chmod(directory,0o700)
        principal=AuthenticatedPrincipal("owner","redmi9","controller")
        authority=SessionAuthority("owner","redmi9",identity_verifier=lambda p:p is principal)
        authority.authorize(principal)
        key=secrets.token_bytes(32)
        signer=TrustedSigner(authority,key)
        calls=[]
        verifier=ProtectedVerifier(key,"owner","redmi9",
            dispatch=lambda action,payload:calls.append((action,payload)) or True)
        verifier.provision(authority,principal)
        endpoint=SignedInputSocket(directory/"input.sock",verifier,peer_uid=os.getuid())
        yield directory,principal,authority,signer,verifier,endpoint,calls
        endpoint.stop()


def send(path,packet):
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as conn:
        conn.settimeout(2)
        conn.connect(str(path))
        conn.sendall(packet)
        return conn.recv(128)


def test_signed_only_and_replay(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.start()
    packet=signer.sign(principal,InputGrant("key_down",{"key":30}))
    assert send(endpoint.path,b"key_down 30\n")==b"DENIED\n"
    assert send(endpoint.path,packet)==b"OK\n"
    assert send(endpoint.path,packet)==b"DENIED\n"
    assert calls==[("key_down",{"key":30})]


def test_reject_unsafe_action_even_if_signed(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.start()
    packet=signer.sign(principal,InputGrant("key_down",{"key":116}))
    assert send(endpoint.path,packet)==b"DENIED\n"
    assert not calls


def test_peer_uid_mismatch_fails_closed(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.peer_uid=os.getuid()+1
    endpoint.start()
    packet=signer.sign(principal,InputGrant("move",{"x":1,"y":1}))
    try:
        result=send(endpoint.path,packet)
    except (ConnectionResetError,BrokenPipeError):
        result=b"DENIED\n"  # kernel may reset a rejected unread SOCK_SEQPACKET
    assert result==b"DENIED\n"
    assert not calls


def test_reject_oversized_packet(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.start()
    assert send(endpoint.path,b"x"*3000)==b"DENIED\n"
    assert not calls


def test_existing_socket_never_replaced(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.path.write_text("not ours")
    with pytest.raises(SocketBoundaryError):endpoint.start()
    assert endpoint.path.read_text()=="not ours"


def test_unsafe_directory_denied(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    os.chmod(directory,0o755)
    with pytest.raises(SocketBoundaryError):endpoint.start()


def test_socket_destroyed_after_stop(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.start()
    assert endpoint.path.is_socket()
    endpoint.stop()
    assert not endpoint.path.exists()


def test_session_revocation_rejected(harness):
    directory,principal,authority,signer,verifier,endpoint,calls=harness
    endpoint.start()
    packet=signer.sign(principal,InputGrant("move",{"x":1,"y":1}))
    verifier.revoke()
    assert send(endpoint.path,packet)==b"DENIED\n"
    assert not calls
