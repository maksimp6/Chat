"""Versioned Alice Input grant protocol; isolated, no real input.

SECURITY MODEL: AuthenticatedPrincipal can only be minted by trusted Live
Server code after owner authentication. Root process must independently verify
the signed grant before touching uinput. This module cannot authenticate a
network user by itself; do not expose its constructors/signing as public RPC.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

MAX_PACKET = 2048
TTL_NS = 300_000_000
ACTIONS = frozenset({"move","click","right","scroll","down","up",
                     "key_down","key_up","text","home","back","recents","open"})


class GrantError(ValueError):
    pass


def _canonical(value: dict) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Trusted server-owned identity; not accepted from HTTP JSON."""
    owner: str
    device: str
    role: str


@dataclass(frozen=True)
class InputGrant:
    action: str
    payload: dict


class SessionAuthority:
    """One controller lease per device; only authenticated owner may issue."""
    def __init__(self, owner: str, device: str, *, clock: Callable[[],int] | None=None):
        if not owner or not device:
            raise GrantError("owner/device required")
        self.owner,self.device=owner,device
        self.clock=clock or time.monotonic_ns
        self.lock=threading.RLock()
        self.epoch=secrets.token_hex(16)
        self.session=None
        self.lease_until=0

    def authorize(self, principal: AuthenticatedPrincipal, lease_ns: int=10_000_000_000) -> str:
        if (type(principal) is not AuthenticatedPrincipal or
            (principal.owner,principal.device,principal.role)!=(self.owner,self.device,"controller") or
            type(lease_ns) is not int or not 0<lease_ns<=60_000_000_000):
            raise GrantError("controller authorization required")
        with self.lock:
            self.epoch=secrets.token_hex(16)
            self.session=secrets.token_hex(16)
            self.lease_until=self.clock()+lease_ns
            return self.session

    def active(self, principal: AuthenticatedPrincipal) -> bool:
        with self.lock:
            return (type(principal) is AuthenticatedPrincipal and
                    (principal.owner,principal.device,principal.role)==(self.owner,self.device,"controller") and
                    self.session is not None and self.clock()<self.lease_until)

    def revoke(self) -> None:
        with self.lock:
            self.session=None
            self.lease_until=0
            self.epoch=secrets.token_hex(16)


class TrustedSigner:
    """Only constructed inside the authenticated server with protected key."""
    def __init__(self, authority: SessionAuthority, key: bytes):
        if type(key) is not bytes or len(key)!=32:
            raise GrantError("32-byte HMAC key required")
        self.authority=authority
        self._key=key
        self.lock=threading.Lock()
        self.seq=0
        self.bound_session=None

    def sign(self, principal: AuthenticatedPrincipal, grant: InputGrant) -> bytes:
        if type(grant) is not InputGrant or grant.action not in ACTIONS:
            raise GrantError("unsupported action")
        if not self.authority.active(principal):
            raise GrantError("unauthorized or expired lease")
        if type(grant.payload) is not dict:
            raise GrantError("payload must be object")
        if len(_canonical(grant.payload))>512:
            raise GrantError("payload too large")
        with self.lock, self.authority.lock:
            if not self.authority.active(principal):
                raise GrantError("session revoked")
            if self.bound_session!=self.authority.session:
                self.seq=0
                self.bound_session=self.authority.session
            self.seq+=1
            data={"v":1,"owner":self.authority.owner,"device":self.authority.device,
                  "epoch":self.authority.epoch,"session":self.authority.session,
                  "seq":self.seq,"nonce":secrets.token_hex(16),
                  "issued":self.authority.clock(),"action":grant.action,"payload":grant.payload}
            raw=_canonical(data)
            packet=_canonical({"grant":data,"mac":hmac.digest(self._key,raw,"sha256").hex()})
            if len(packet)>MAX_PACKET:
                raise GrantError("packet too large")
            return packet


class ProtectedVerifier:
    """Privileged-side verification; callback is the sole physical dispatch.

    A test instance is NOT equivalent to deployment in a root-owned immutable
    executable with isolated key provisioning and SO_PEERCRED.
    """
    def __init__(self, key: bytes, owner: str, device: str, *, clock=None, dispatch=None):
        if type(key) is not bytes or len(key)!=32:
            raise GrantError("invalid verifier key")
        self._key=key
        self.owner,self.device=owner,device
        self.clock=clock or time.monotonic_ns
        self.dispatch=dispatch or (lambda action,payload:False)
        self.lock=threading.RLock()
        self.epoch=None
        self.session=None
        self.lease_until=0
        self.seq=0

    def provision(self, authority: SessionAuthority, principal: AuthenticatedPrincipal) -> None:
        """Lab-only direct handoff; production requires authenticated root channel."""
        if not authority.active(principal):
            raise GrantError("cannot provision unauthenticated session")
        with self.lock, authority.lock:
            self.epoch,self.session=authority.epoch,authority.session
            self.lease_until=authority.lease_until
            self.seq=0

    def accept(self, packet: bytes) -> bool:
        if type(packet) is not bytes or not 0<len(packet)<=MAX_PACKET:
            raise GrantError("invalid packet")
        try:
            outer=json.loads(packet)
            if type(outer) is not dict or set(outer)!={"grant","mac"}:
                raise ValueError()
            data=outer["grant"]
            if type(data) is not dict or set(data)!={"v","owner","device","epoch","session","seq","nonce","issued","action","payload"}:
                raise ValueError()
            mac=bytes.fromhex(outer["mac"])
            if len(mac)!=32 or not hmac.compare_digest(mac,hmac.digest(self._key,_canonical(data),"sha256")):
                raise ValueError()
            if (type(data["v"]) is not int or data["v"]!=1 or
                data["owner"]!=self.owner or data["device"]!=self.device or
                type(data["seq"]) is not int or data["seq"]<1 or
                type(data["issued"]) is not int or
                type(data["nonce"]) is not str or len(data["nonce"])!=32 or
                any(c not in "0123456789abcdef" for c in data["nonce"]) or
                data["action"] not in ACTIONS or type(data["payload"]) is not dict or
                len(_canonical(data["payload"]))>512):
                raise ValueError()
            age=self.clock()-data["issued"]
            if not 0<=age<=TTL_NS:
                raise ValueError()
        except (ValueError,TypeError,KeyError,OverflowError,UnicodeError) as exc:
            raise GrantError("invalid signed grant") from exc
        with self.lock:
            if (self.session is None or self.clock()>=self.lease_until or
                data["session"]!=self.session or data["epoch"]!=self.epoch or
                data["seq"]<=self.seq):
                raise GrantError("expired, revoked or replayed session")
            self.seq=data["seq"]
            return bool(self.dispatch(data["action"],data["payload"]))

    def revoke(self) -> None:
        with self.lock:
            self.epoch=self.session=None
            self.lease_until=0
            self.seq=0
