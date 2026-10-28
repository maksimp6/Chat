"""Fail-closed contract for staging a Bambu A1 mini print artifact.

This module validates artifact metadata only. It cannot connect to a printer,
upload files, or start a physical print. The transport is a separate capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import file_digest, sha256
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile, ZipFile

MAX_JOB_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 256
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


class InvalidPrintJob(ValueError):
    """An artifact is not approved for delivery."""


@dataclass(frozen=True)
class ApprovedJob:
    path: Path
    digest: str
    size: int
    printer_model: str
    approved_profile: str


class UploadTransport(Protocol):
    """Authorized local agent, not a GitHub-hosted runner."""

    def stage(self, job: ApprovedJob) -> str: ...
    def verify(self, remote_name: str, size: int) -> bool: ...


def validate_job(path: Path, *, printer_model: str, approved_profile: str) -> ApprovedJob:
    if printer_model != "A1 mini" or not approved_profile:
        raise InvalidPrintJob("printer model or approved profile missing")
    if path.suffix.lower() != ".3mf" or not path.name.endswith(".gcode.3mf"):
        raise InvalidPrintJob("expected sliced .gcode.3mf")
    if path.is_symlink() or not path.is_file():
        raise InvalidPrintJob("artifact must be a regular non-symlink file")
    size = path.stat().st_size
    if not 0 < size <= MAX_JOB_BYTES:
        raise InvalidPrintJob("artifact size out of bounds")
    with path.open("rb") as source:
        digest = file_digest(source, sha256)
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_MEMBERS:
                raise InvalidPrintJob("invalid member count")
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise InvalidPrintJob("uncompressed size out of bounds")
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise InvalidPrintJob("duplicate archive members")
            if any(
                name.startswith("/") or "\\" in name or ".." in Path(name).parts for name in names
            ):
                raise InvalidPrintJob("unsafe archive path")
            if "[Content_Types].xml" not in names:
                raise InvalidPrintJob("missing 3MF manifest")
            if not any(name.lower().endswith(".gcode") for name in names):
                raise InvalidPrintJob("missing sliced G-code")
    except BadZipFile as exc:
        raise InvalidPrintJob("invalid 3MF archive") from exc
    return ApprovedJob(path, digest.hexdigest(), size, printer_model, approved_profile)


def stage_only(job: ApprovedJob, transport: UploadTransport) -> str:
    """Stage a verified job without issuing any print-start command."""
    if not job.digest or job.size <= 0:
        raise InvalidPrintJob("invalid approval")
    remote_name = transport.stage(job)
    if not remote_name or not transport.verify(remote_name, job.size):
        raise InvalidPrintJob("remote artifact not verified")
    return remote_name
