"""RED: provision must revalidate owner lease while authority is locked."""
import secrets
import pytest
from alice_mouse.security import AuthenticatedPrincipal,SessionAuthority,ProtectedVerifier,GrantError


def test_provision_rechecks_auth_inside_lock(monkeypatch):
    principal=AuthenticatedPrincipal('owner','redmi9','controller')
    authority=SessionAuthority('owner','redmi9',identity_verifier=lambda p:p is principal)
    authority.authorize(principal)
    verifier=ProtectedVerifier(secrets.token_bytes(32),'owner','redmi9')
    calls=[0]
    def active(_):
        calls[0]+=1
        return calls[0]==1
    monkeypatch.setattr(authority,'active',active)
    with pytest.raises(GrantError):verifier.provision(authority,principal)
    assert verifier.session is None


def test_provision_rejects_expired_lease_during_handoff():
    now=[1000]
    principal=AuthenticatedPrincipal('owner','redmi9','controller')
    authority=SessionAuthority('owner','redmi9',clock=lambda:now[0],identity_verifier=lambda p:p is principal)
    authority.authorize(principal,lease_ns=100)
    verifier=ProtectedVerifier(secrets.token_bytes(32),'owner','redmi9',clock=lambda:now[0])
    now[0]=1100
    with pytest.raises(GrantError):verifier.provision(authority,principal)
