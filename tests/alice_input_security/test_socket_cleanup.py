import pytest
from alice_mouse.security.socket_boundary import SocketBoundaryError
from test_socket_boundary import harness

def test_chmod_failure_unlinks_owned_socket(harness,monkeypatch):
    import alice_mouse.security.socket_boundary as module
    _,_,_,_,_,endpoint,_=harness
    original=module.os.chmod
    def fail(path,mode,**kwargs):
        if str(path) in (str(endpoint.path),endpoint.path.name):
            raise PermissionError("injected chmod failure")
        return original(path,mode,**kwargs)
    monkeypatch.setattr(module.os,"chmod",fail)
    with pytest.raises(PermissionError):
        endpoint.start()
    assert not endpoint.path.exists()


def test_existing_foreign_path_preserved(harness):
    _,_,_,_,_,endpoint,_=harness
    endpoint.path.write_text("foreign")
    with pytest.raises(SocketBoundaryError):
        endpoint.start()
    assert endpoint.path.read_text()=="foreign"
