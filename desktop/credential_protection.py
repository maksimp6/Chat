"""Windows-protected credential key provisioning for the desktop client."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets


KEY_FILE_NAME = "provider-credential-key.dpapi"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DesktopCredentialProtectionError(RuntimeError):
    """Raised when the desktop credential key cannot be protected safely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt32():
    if os.name != "nt":
        raise DesktopCredentialProtectionError("Windows DPAPI is required for desktop credentials")
    return ctypes.windll.crypt32, ctypes.windll.kernel32


def protect_for_current_user(value: str) -> bytes:
    crypt32, kernel32 = _crypt32()
    source, _source_buffer = _blob_from_bytes(value.encode("utf-8"))
    protected = _DataBlob()

    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Alice Pro provider credential key",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    )
    if not ok:
        raise DesktopCredentialProtectionError("Windows DPAPI failed to protect credential key")

    try:
        return _bytes_from_blob(protected)
    finally:
        kernel32.LocalFree(protected.pbData)


def unprotect_for_current_user(value: bytes) -> str:
    crypt32, kernel32 = _crypt32()
    source, _source_buffer = _blob_from_bytes(value)
    plaintext = _DataBlob()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(plaintext),
    )
    if not ok:
        raise DesktopCredentialProtectionError("Windows DPAPI failed to unlock credential key")

    try:
        return _bytes_from_blob(plaintext).decode("utf-8")
    finally:
        kernel32.LocalFree(plaintext.pbData)


def ensure_provider_credential_key(
    data_dir: Path,
    *,
    protect=protect_for_current_user,
    unprotect=unprotect_for_current_user,
) -> str:
    configured = os.environ.get("ALICE_PROVIDER_CREDENTIAL_KEY", "").strip()
    if configured:
        return configured

    if os.name != "nt":
        raise DesktopCredentialProtectionError(
            "ALICE_PROVIDER_CREDENTIAL_KEY must be configured outside Windows desktop"
        )

    key_path = data_dir / KEY_FILE_NAME
    if key_path.exists():
        try:
            protected = base64.b64decode(key_path.read_bytes(), validate=True)
            key = unprotect(protected)
        except Exception as exc:
            raise DesktopCredentialProtectionError(
                "Unable to unlock the protected desktop credential key"
            ) from exc
        if not key:
            raise DesktopCredentialProtectionError("Protected desktop credential key is empty")
    else:
        key = secrets.token_urlsafe(48)
        protected = protect(key)
        encoded = base64.b64encode(protected)
        tmp_path = key_path.with_suffix(".tmp")
        tmp_path.write_bytes(encoded)
        os.replace(tmp_path, key_path)

    os.environ["ALICE_PROVIDER_CREDENTIAL_KEY"] = key
    return key
