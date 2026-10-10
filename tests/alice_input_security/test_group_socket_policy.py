import os
import stat
import pytest
from alice_mouse.security.socket_boundary import SignedInputSocket,SocketBoundaryError
from test_socket_boundary import harness,send
from alice_mouse.security import InputGrant


def test_shared_group_socket_permissions(harness):
    directory,principal,authority,signer,verifier,_,calls=harness
    os.chmod(directory,0o710)
    endpoint=SignedInputSocket(directory/"shared.sock",verifier,peer_uid=os.getuid(),shared_gid=os.getgid())
    try:
        endpoint.start()
        info=endpoint.path.stat()
        assert stat.S_IMODE(info.st_mode)==0o660
        assert info.st_gid==os.getgid()
        packet=signer.sign(principal,InputGrant("move",{"x":1,"y":2}))
        assert send(endpoint.path,packet)==b"OK\n"
        assert calls==[("move",{"x":1,"y":2})]
    finally:
        endpoint.stop()


@pytest.mark.parametrize("mode",[0o700,0o770,0o750,0o777])
def test_shared_group_requires_exact_nonwritable_directory(harness,mode):
    directory,_,_,_,verifier,_,_=harness
    os.chmod(directory,mode)
    endpoint=SignedInputSocket(directory/"shared.sock",verifier,peer_uid=os.getuid(),shared_gid=os.getgid())
    with pytest.raises(SocketBoundaryError):
        endpoint.start()


def test_wrong_group_denied(harness):
    directory,_,_,_,verifier,_,_=harness
    os.chmod(directory,0o710)
    endpoint=SignedInputSocket(directory/"shared.sock",verifier,peer_uid=os.getuid(),shared_gid=os.getgid()+1)
    with pytest.raises(SocketBoundaryError):
        endpoint.start()


def test_shared_mode_still_checks_peer_uid(harness):
    directory,principal,_,signer,verifier,_,calls=harness
    os.chmod(directory,0o710)
    endpoint=SignedInputSocket(directory/"shared.sock",verifier,peer_uid=os.getuid()+1,shared_gid=os.getgid())
    try:
        endpoint.start()
        packet=signer.sign(principal,InputGrant("move",{"x":1,"y":2}))
        try:
            result=send(endpoint.path,packet)
        except (ConnectionResetError,BrokenPipeError):
            result=b"DENIED\n"
        assert result==b"DENIED\n"
        assert not calls
    finally:
        endpoint.stop()
