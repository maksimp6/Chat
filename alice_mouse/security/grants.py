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
    def __init__(self, owner: str, device: str, *, clock: Callable[[],int] | None=None,
                 identity_verifier: Callable[[AuthenticatedPrincipal],bool] | None=None):
        if not owner or not device:
            raise GrantError("owner/device required")
        self.owner,self.device=owner,device
        self.clock=clock or time.monotonic_ns
        # Fail closed without a trusted, server-supplied identity decision.
        # The public principal dataclass is metadata, NOT proof of login.
        self._identity_verifier=identity_verifier
        self.lock=threading.RLock()
        self.epoch=secrets.token_hex(16)
        self.session=None
        self.lease_until=0

    def authorize(self, principal: AuthenticatedPrincipal, lease_ns: int=10_000_000_000) -> str:
        if (type(principal) is not AuthenticatedPrincipal or
            (principal.owner,principal.device,principal.role)!=(self.owner,self.device,"controller") or
            self._identity_verifier is None or
            self._identity_verifier(principal) is not True or
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
                    self._identity_verifier is not None and
                    self._identity_verifier(principal) is True and
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


def _validate_action_payload(action: str, payload: dict) -> None:
    """Root-side, fail-closed v1 action schema; no shell or client-selected role."""
    import re
    if type(payload) is not dict:
        raise GrantError("invalid payload")
    if action in ("move", "click"):
        if set(payload)!={"x","y"} or any(type(payload[k]) is not int or not -500<=payload[k]<=500 for k in ("x","y")):
            raise GrantError("invalid coordinates")
    elif action in ("right", "down", "up"):
        if set(payload)!={"button"} or type(payload["button"]) is not int or payload["button"] not in (1,2):
            raise GrantError("invalid button")
    elif action=="scroll":
        if set(payload)!={"delta"} or type(payload["delta"]) is not int or not -20<=payload["delta"]<=20:
            raise GrantError("invalid scroll")
    elif action in ("key_down","key_up"):
        # Restricted safe subset of evdev keycodes, excluding power/system keys.
        allowed={1,14,15,28,57,97,100,102,103,104,105,106,107,108,109,110,111,113,114,115,
                 29,42,54,56,125,126}
        allowed.update(range(2,14))
        allowed.update(range(16,28))
        allowed.update(range(30,54))
        allowed.update(range(59,69))
        if set(payload)!={"key"} or type(payload["key"]) is not int or payload["key"] not in allowed:
            raise GrantError("invalid key")
    elif action=="open":
        if set(payload)!={"package"} or type(payload["package"]) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",payload["package"]):
            raise GrantError("invalid package")
    elif action=="text":
        if set(payload)!={"text"} or type(payload["text"]) is not str or not 0<len(payload["text"])<=256 or any(ord(c)<32 and c not in "\n\t" for c in payload["text"]):
            raise GrantError("invalid text")
    elif action in ("home","back","recents"):
        if payload:
            raise GrantError("unexpected payload")
    else:
        raise GrantError("unsupported action")


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
        self.shutdown_requested=threading.Event()

    def provision(self, authority: SessionAuthority, principal: AuthenticatedPrincipal) -> None:
        """Lab-only direct handoff; production requires authenticated root channel."""
        if not authority.active(principal):
            raise GrantError("cannot provision unauthenticated session")
        with self.lock, authority.lock:
            # The owner lease may be revoked between the first check and
            # acquisition of the two locks. Never provision stale identity.
            if not authority.active(principal):
                raise GrantError("controller lease changed during provisioning")
            self.shutdown_requested.clear()
            self.epoch,self.session=authority.epoch,authority.session
            self.lease_until=authority.lease_until
            self.seq=0

    def accept(self, packet: bytes) -> bool:
        if self.shutdown_requested.is_set():
            raise GrantError("verifier shut down")
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
            _validate_action_payload(data["action"],data["payload"])
            age=self.clock()-data["issued"]
            if not 0<=age<=TTL_NS:
                raise ValueError()
        except (ValueError,TypeError,KeyError,OverflowError,UnicodeError) as exc:
            raise GrantError("invalid signed grant") from exc
        with self.lock:
            if (self.shutdown_requested.is_set() or self.session is None or self.clock()>=self.lease_until or
                data["session"]!=self.session or data["epoch"]!=self.epoch or
                data["seq"]<=self.seq):
                raise GrantError("expired, revoked or replayed session")
            self.seq=data["seq"]
            try:
                accepted=self.dispatch(data["action"],data["payload"])
            except Exception as exc:
                # Physical dispatch may have partially executed. Revoke this
                # session so no further input is permitted after a fault.
                self.revoke()
                raise GrantError("input backend failed; session revoked") from exc
            if self.shutdown_requested.is_set():
                self.revoke()
                raise GrantError("verifier shut down during dispatch")
            if accepted is not True:
                self.revoke()
                raise GrantError("input backend denied; session revoked")
            return True

    def request_shutdown(self) -> None:
        # Does not wait for an in-flight backend callback to finish.
        self.shutdown_requested.set()

    def revoke(self) -> None:
        with self.lock:
            self.epoch=self.session=None
            self.lease_until=0
            self.seq=0
