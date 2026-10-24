import concurrent.futures
import json
import secrets
import threading
import pytest

from alice_mouse.security import (
    AuthenticatedPrincipal, SessionAuthority, InputGrant,
    GrantError, ProtectedVerifier, TrustedSigner,
)


@pytest.fixture
def context():
    now=[1_000_000_000]
    clock=lambda:now[0]
    owner=AuthenticatedPrincipal("owner-a","redmi9","controller")
    authority=SessionAuthority("owner-a","redmi9",clock=clock,
        identity_verifier=lambda candidate:candidate is owner)
    authority.authorize(owner)
    key=secrets.token_bytes(32)
    events=[]
    signer=TrustedSigner(authority,key)
    verifier=ProtectedVerifier(key,"owner-a","redmi9",clock=clock,
        dispatch=lambda action,payload:events.append((action,payload)) or True)
    verifier.provision(authority,owner)
    return now,owner,authority,signer,verifier,events


def signed(c,action="move",payload=None):
    _,owner,_,signer,_,_=c
    return signer.sign(owner,InputGrant(action,{"x":5,"y":1} if payload is None else payload))


def test_valid_grant_dispatched_once(context):
    p=signed(context)
    assert context[4].accept(p)
    assert context[5]==[("move",{"x":5,"y":1})]
    with pytest.raises(GrantError):context[4].accept(p)


@pytest.mark.parametrize("role,owner,device",[
    ("viewer","owner-a","redmi9"),("admin","owner-a","redmi9"),
    ("controller","other","redmi9"),("controller","owner-a","other")
])
def test_wrong_principal_denied(context,role,owner,device):
    with pytest.raises(GrantError):
        context[3].sign(AuthenticatedPrincipal(owner,device,role),InputGrant("move",{}))


def test_revocation_and_reissue(context):
    old=signed(context)
    now,principal,authority,signer,verifier,events=context
    authority.revoke()
    authority.authorize(principal)
    verifier.provision(authority,principal)
    with pytest.raises(GrantError):verifier.accept(old)
    assert verifier.accept(signed(context))


def test_expired_grant(context):
    packet=signed(context)
    context[0][0]+=300_000_001
    with pytest.raises(GrantError):context[4].accept(packet)
    assert not context[5]


def test_session_lease_expired(context):
    context[0][0]+=11_000_000_000
    with pytest.raises(GrantError):signed(context)


def test_tamper_rejected(context):
    p=json.loads(signed(context))
    p["grant"]["payload"]["x"]=999
    with pytest.raises(GrantError):context[4].accept(json.dumps(p).encode())


def test_wrong_device_rejected(context):
    now,owner,authority,signer,_,events=context
    wrong=ProtectedVerifier(secrets.token_bytes(32),"owner-a","other",clock=lambda:now[0])
    with pytest.raises(GrantError):wrong.accept(signed(context))


def test_concurrent_replay_single_dispatch(context):
    packet=signed(context)
    def once(_):
        try:return context[4].accept(packet)
        except GrantError:return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        result=list(pool.map(once,range(12)))
    assert result.count(True)==1
    assert len(context[5])==1


def test_out_of_order_sequence(context):
    old=signed(context)
    new=signed(context)
    assert context[4].accept(new)
    with pytest.raises(GrantError):context[4].accept(old)


def test_missing_session_denied(context):
    context[4].revoke()
    with pytest.raises(GrantError):context[4].accept(signed(context))


def test_bad_lease_not_accepted():
    auth=SessionAuthority("owner-a","redmi9")
    with pytest.raises(GrantError):
        auth.authorize(AuthenticatedPrincipal("owner-a","redmi9","controller"),lease_ns=0)


def test_no_plaintext_dispatch(context):
    with pytest.raises(GrantError):context[4].accept(b"move 10 10\\n")
    assert not context[5]


def test_payload_too_large(context):
    with pytest.raises(GrantError):
        signed(context,payload={"text":"x"*600})


def test_unsigned_key_press_denied(context):
    with pytest.raises(GrantError):
        context[4].accept(b'{"action":"key_down","key":29}')
