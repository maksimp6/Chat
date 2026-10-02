"""Bounded private native-session checkpoints; ciphertext is safe to transport.

The provider credential is never serialized. HKDF separates its encryption use
from authentication. Rotating that credential deliberately blocks old snapshots.
"""

from __future__ import annotations

import base64
import json
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MAX_PLAINTEXT_BYTES = 16 * 1024 * 1024
_MAGIC = b"ALICE-CLAUDE-CHECKPOINT\x01"
_NONCE_BYTES = 12
_OVERHEAD = len(_MAGIC) + _NONCE_BYTES + 16
_FIELDS = {"repo_id", "session_id", "native_session_id", "schema", "generation"}


def _identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("checkpoint_invalid")


def _key_and_scope(secret, binding):
    if (
        not isinstance(secret, str)
        or not secret
        or not isinstance(binding, dict)
        or set(binding) != _FIELDS
    ):
        raise ValueError("checkpoint_invalid")
    for field in ("repo_id", "generation"):
        if type(binding[field]) is not int or binding[field] <= 0:
            raise ValueError("checkpoint_invalid")
    if type(binding["schema"]) is not int or binding["schema"] != 1:
        raise ValueError("checkpoint_invalid")
    _identity(binding["session_id"])
    _identity(binding["native_session_id"])
    scope = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"alice-pro/claude-checkpoint/v1",
        info=b"native-transcript/AES-256-GCM/provider-credential",
    ).derive(secret.encode())
    return key, _MAGIC + scope


def seal_checkpoint(plaintext: bytes, secret: str, binding: dict) -> bytes:
    try:
        if not isinstance(plaintext, bytes) or len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise ValueError("checkpoint_invalid")
        key, scope = _key_and_scope(secret, binding)
        nonce = os.urandom(_NONCE_BYTES)
        return _MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, scope)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("checkpoint_invalid") from None


def open_checkpoint(ciphertext: bytes, secret: str, binding: dict) -> bytes:
    try:
        if (
            not isinstance(ciphertext, bytes)
            or not _OVERHEAD <= len(ciphertext) <= MAX_PLAINTEXT_BYTES + _OVERHEAD
        ):
            raise ValueError("checkpoint_invalid")
        if not ciphertext.startswith(_MAGIC):
            raise ValueError("checkpoint_invalid")
        key, scope = _key_and_scope(secret, binding)
        offset = len(_MAGIC)
        nonce = ciphertext[offset : offset + _NONCE_BYTES]
        return AESGCM(key).decrypt(nonce, ciphertext[offset + _NONCE_BYTES :], scope)
    except (InvalidTag, ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("checkpoint_invalid") from None


def pack_checkpoint(state: dict, native_session_id: str, transcript: bytes) -> bytes:
    try:
        _identity(native_session_id)
        if not isinstance(state, dict) or not isinstance(transcript, bytes):
            raise ValueError("checkpoint_invalid")
        envelope = {
            "schema": 1,
            "state": state,
            "native_session_id": native_session_id,
            "transcript_b64": base64.b64encode(transcript).decode("ascii"),
        }
        payload = json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(payload) > MAX_PLAINTEXT_BYTES:
            raise ValueError("checkpoint_invalid")
        return payload
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("checkpoint_invalid") from None


def unpack_checkpoint(payload: bytes) -> dict:
    try:
        if not isinstance(payload, bytes) or len(payload) > MAX_PLAINTEXT_BYTES:
            raise ValueError("checkpoint_invalid")
        envelope = json.loads(payload)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema",
            "state",
            "native_session_id",
            "transcript_b64",
        }:
            raise ValueError("checkpoint_invalid")
        if (
            type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or not isinstance(envelope["state"], dict)
        ):
            raise ValueError("checkpoint_invalid")
        _identity(envelope["native_session_id"])
        transcript = base64.b64decode(envelope["transcript_b64"], validate=True)
        return {
            "state": envelope["state"],
            "native_session_id": envelope["native_session_id"],
            "transcript": transcript,
        }
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("checkpoint_invalid") from None
