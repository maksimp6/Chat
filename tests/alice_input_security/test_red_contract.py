"""RED contract: untrusted user-controlled principal must never mint input grants.

These tests intentionally require a trusted identity capability not present
in the current #1090 implementation. Keep RED until the auth boundary exists.
"""
import secrets
import pytest

from alice_mouse.security import (
    AuthenticatedPrincipal, SessionAuthority, TrustedSigner, InputGrant,
    ProtectedVerifier, GrantError,
)


def test_forged_controller_fields_cannot_issue():
    authority=SessionAuthority("owner-a","redmi9")
    forged=AuthenticatedPrincipal("owner-a","redmi9","controller")
    with pytest.raises(GrantError):
        authority.authorize(forged)


@pytest.mark.parametrize("action,payload",[
    ("move",{"x":999999,"y":0}),
    ("key_down",{"key":116}),  # KEY_POWER must not be accepted
    ("key_up",{"key":"KEY_A; sh"}),
    ("open",{"package":"com.example;rm -rf /"}),
    ("text",{"text":"hello","extra":"unexpected"}),
    ("click",{"x":True,"y":0}),
])
def test_root_verifier_rejects_signed_invalid_action_payload(action,payload):
    key=secrets.token_bytes(32)
    principal=AuthenticatedPrincipal("owner-a","redmi9","controller")
    authority=SessionAuthority("owner-a","redmi9")
    authority.authorize(principal)
    signer=TrustedSigner(authority,key)
    dispatched=[]
    verifier=ProtectedVerifier(key,"owner-a","redmi9",
        dispatch=lambda a,p:dispatched.append((a,p)) or True)
    verifier.provision(authority,principal)
    packet=signer.sign(principal,InputGrant(action,payload))
    with pytest.raises(GrantError):
        verifier.accept(packet)
    assert not dispatched
