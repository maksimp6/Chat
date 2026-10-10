"""RED: stop must prevent further physical dispatch after shutdown begins."""
import threading
import time
from alice_mouse.security import InputGrant
from test_socket_boundary import harness, send


def test_stop_revokes_before_waiting_for_slow_dispatch(harness):
    _, principal, _, signer, verifier, endpoint, calls = harness
    entered = threading.Event()
    release = threading.Event()
    def slow(action, payload):
        entered.set()
        release.wait(2)
        return True
    verifier.dispatch = slow
    endpoint.start()
    first = signer.sign(principal, InputGrant('move', {'x':1,'y':1}))
    responses=[]
    def request():
        try:responses.append(send(endpoint.path,first))
        except OSError:responses.append(b"CLOSED")
    worker = threading.Thread(target=request,daemon=True)
    worker.start()
    assert entered.wait(1)
    errors=[]
    def shutdown():
        try:endpoint.stop()
        except Exception as exc:errors.append(exc)
    stopper = threading.Thread(target=shutdown,daemon=True)
    stopper.start()
    time.sleep(0.1)
    assert verifier.shutdown_requested.is_set(), 'shutdown must mark verifier closed immediately'
    release.set()
    stopper.join(3)
    worker.join(3)
    assert not stopper.is_alive()
    assert not errors
