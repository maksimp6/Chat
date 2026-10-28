"""Read device credentials only from a pinned, owner-private local directory.

No credentials are embedded in source, environment variables or command-line
arguments.  Caller decides the path; this module never creates a password.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
import os
from pathlib import Path
import stat

from .bambu_lan import _certificate_fingerprint, printer_report_topic, read_live_status


@dataclass(frozen=True)
class LocalBambuProfile:
    host: str
    serial: str
    access_code: str
    trusted_cert_pem: str


def _read_private_file(dirfd: int, name: str, max_bytes: int) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(name, flags, dir_fd=dirfd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise PermissionError("printer profile file must be owner-private")
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("printer profile value is too large")
        return data.decode("utf-8").strip()
    finally:
        os.close(fd)


def load_local_profile(directory: str | Path) -> LocalBambuProfile:
    """Fail closed on missing secret, symlinks, wrong UID or public permissions."""
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(dirfd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise PermissionError("printer profile directory must be mode 0700")
        serial = _read_private_file(dirfd, "serial", 64)
        host = _read_private_file(dirfd, "host", 64)
        code = _read_private_file(dirfd, "access_code", 32)
        pem = _read_private_file(dirfd, "printer.pem", 16384)
    finally:
        os.close(dirfd)
    printer_report_topic(serial)
    try:
        ip = IPv4Address(host)
    except ValueError as exc:
        raise ValueError("printer host must be a LAN IPv4 address") from exc
    if not ip.is_private or ip.is_loopback or ip.is_multicast:
        raise ValueError("printer host must be a private LAN address")
    if len(code) != 8 or not code.isascii() or not code.isalnum():
        raise ValueError("invalid printer access code format")
    _certificate_fingerprint(pem)
    return LocalBambuProfile(
        host=host, serial=serial, access_code=code, trusted_cert_pem=pem
    )


def read_status_from_local_profile(
    owner_id: str, directory: str | Path, *, timeout: float = 20
) -> dict[str, object]:
    """Use only the local protected profile for real device authentication."""
    profile = load_local_profile(directory)
    return read_live_status(
        owner_id,
        profile.serial,
        profile.access_code,
        host=profile.host,
        trusted_cert_pem=profile.trusted_cert_pem,
        timeout=timeout,
    )
