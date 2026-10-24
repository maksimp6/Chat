"""Root-side action schema acceptance, independent of auth-minting RED."""
import secrets
import pytest
from alice_mouse.security import AuthenticatedPrincipal, SessionAuthority, TrustedSigner, InputGrant, ProtectedVerifier, GrantError

@pytest.mark.parametrize('action,payload',[
    ('move',{'x':-500,'y':500}),
    ('click',{'x':0,'y':0}),
    ('right',{'button':2}),
    ('down',{'button':1}),
    ('up',{'button':1}),
    ('scroll',{'delta':-20}),
    ('key_down',{'key':30}),
    ('key_up',{'key':30}),
    ('open',{'package':'org.example.app'}),
    ('text',{'text':'Привет 😀'}),
    ('home',{}),('back',{}),('recents',{}),
])
def test_valid_signed_action_reaches_callback(action,payload,monkeypatch):
    principal=AuthenticatedPrincipal('owner','redmi9','controller')
    authority=SessionAuthority('owner','redmi9')
    monkeypatch.setattr(authority,'active',lambda _principal:True)
    authority.session=secrets.token_hex(16)
    authority.lease_until=authority.clock()+10_000_000_000
    key=secrets.token_bytes(32)
    signer=TrustedSigner(authority,key)
    calls=[]
    verifier=ProtectedVerifier(key,'owner','redmi9',dispatch=lambda a,p:calls.append((a,p)) or True)
    verifier.provision(authority,principal)
    assert verifier.accept(signer.sign(principal,InputGrant(action,payload)))
    assert calls==[(action,payload)]

@pytest.mark.parametrize('action,payload',[
    ('move',{'x':1,'y':2,'command':'shell'}),
    ('key_down',{'key':116}),
    ('key_up',{'key':True}),
    ('open',{'package':'com.example;sh'}),
    ('text',{'text':'ok','password':'secret'}),
    ('back',{'package':'com.example.app'}),
    ('scroll',{'delta':100}),
])
def test_action_schema_rejects_extra_or_unsafe_fields(action,payload):
    from alice_mouse.security.grants import _validate_action_payload
    with pytest.raises(GrantError):_validate_action_payload(action,payload)
