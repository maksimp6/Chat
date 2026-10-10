import os
import pytest
from alice_mouse.security.socket_boundary import SignedInputSocket, SocketBoundaryError
from alice_mouse.security import InputGrant
from test_socket_boundary import harness, send


def test_restart_serves_second_session(harness):
    _,principal,_,signer,_,endpoint,calls=harness
    endpoint.start()
    endpoint.stop()
    endpoint.start()
    packet=signer.sign(principal,InputGrant("move",{"x":3,"y":4}))
    assert send(endpoint.path,packet)==b"OK\n"
    assert calls==[("move",{"x":3,"y":4})]


@pytest.mark.parametrize("failure",["chown","listen"])
def test_group_setup_failure_removes_own_socket(harness,monkeypatch,failure):
    import alice_mouse.security.socket_boundary as module
    directory,_,_,_,verifier,_,_=harness
    os.chmod(directory,0o710)
    endpoint=SignedInputSocket(directory/"group.sock",verifier,
                               peer_uid=os.getuid(),shared_gid=os.getgid())
    if failure=="chown":
        original=module.os.chown
        def fail(path,uid,gid):
            if str(path)==str(endpoint.path):
                raise PermissionError("injected chown failure")
            return original(path,uid,gid)
        monkeypatch.setattr(module.os,"chown",fail)
    else:
        original=module.socket.socket
        class FailListen:
            def __init__(self,*args,**kwargs):
                self.delegate=original(*args,**kwargs)
            def __getattr__(self,name):
                return getattr(self.delegate,name)
            def listen(self,*args):
                raise OSError("injected listen failure")
        monkeypatch.setattr(module.socket,"socket",FailListen)
    with pytest.raises((OSError,PermissionError)):
        endpoint.start()
    assert not endpoint.path.exists()


def test_directory_swap_before_bind_is_rejected(harness,monkeypatch):
    import alice_mouse.security.socket_boundary as module
    directory,_,_,_,verifier,endpoint,_=harness
    original=module._check_directory
    moved=directory.parent/(directory.name+"-moved")
    def swap(path,**kwargs):
        original(path,**kwargs)
        directory.rename(moved)
        directory.mkdir(mode=0o700)
    monkeypatch.setattr(module,"_check_directory",swap)
    try:
        with pytest.raises(SocketBoundaryError):
            endpoint.start()
        assert not endpoint.path.exists()
    finally:
        if directory.exists():
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()
        moved.rename(directory)


def test_stop_after_parent_replacement_never_unlinks_foreign_socket(harness):
    directory,_,_,_,_,endpoint,_=harness
    endpoint.start()
    moved=directory.parent/(directory.name+'-held')
    directory.rename(moved)
    directory.mkdir(mode=0o700)
    foreign=directory/'input.sock'
    foreign.write_text('foreign marker')
    try:
        endpoint.stop()
        assert foreign.read_text()=='foreign marker'
        assert not (moved/'input.sock').exists()
    finally:
        foreign.unlink()
        directory.rmdir()
        moved.rename(directory)


def test_second_start_rejected_without_losing_live_server(harness):
    directory,principal,_,signer,_,endpoint,calls=harness
    endpoint.start()
    with pytest.raises(SocketBoundaryError):
        endpoint.start()
    packet=signer.sign(principal,InputGrant('move',{'x':4,'y':2}))
    assert send(endpoint.path,packet)==b'OK\n'
    assert calls==[('move',{'x':4,'y':2})]
