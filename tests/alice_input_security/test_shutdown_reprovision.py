"""RED: a stopped verifier cannot be resurrected with its old session."""
import secrets
import pytest
from alice_mouse.security import AuthenticatedPrincipal, SessionAuthority, ProtectedVerifier, GrantError


def test_shutdown_rejects_same_epoch_reprovision():
    owner=AuthenticatedPrincipal('owner','redmi9','controller')
    authority=SessionAuthority('owner','redmi9',identity_verifier=lambda p:p is owner)
    authority.authorize(owner)
    verifier=ProtectedVerifier(secrets.token_bytes(32),'owner','redmi9')
    verifier.provision(authority,owner)
    previous=verifier.session
    verifier.request_shutdown()
    verifier.revoke()
    with pytest.raises(GrantError):
        verifier.provision(authority,owner)
    assert verifier.session is None
    authority.authorize(owner)
    verifier.provision(authority,owner)
    assert verifier.session != previous
