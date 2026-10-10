"""RED: shutdown and reprovision cannot interleave to resurrect an old grant."""
import secrets
import threading
import pytest
from alice_mouse.security import AuthenticatedPrincipal,SessionAuthority,ProtectedVerifier,GrantError


def test_shutdown_cannot_be_cleared_during_provision(monkeypatch):
    principal=AuthenticatedPrincipal('owner','redmi9','controller')
    authority=SessionAuthority('owner','redmi9',identity_verifier=lambda p:p is principal)
    authority.authorize(principal)
    verifier=ProtectedVerifier(secrets.token_bytes(32),'owner','redmi9')
    verifier.provision(authority,principal)
    authority.authorize(principal)
    entered=threading.Event()
    resume=threading.Event()
    original_clear=verifier.shutdown_requested.clear
    def pause_clear():
        entered.set()
        assert resume.wait(2)
        original_clear()
    monkeypatch.setattr(verifier.shutdown_requested,'clear',pause_clear)
    errors=[]
    def reprovision():
        try:verifier.provision(authority,principal)
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=reprovision)
    worker.start()
    try:
        assert entered.wait(1)
        verifier.request_shutdown()
    finally:
        resume.set()
        worker.join(3)
    assert not worker.is_alive()
    assert verifier.shutdown_requested.is_set(), 'shutdown must win over concurrent provision'
