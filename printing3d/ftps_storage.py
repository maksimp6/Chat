"""Certificate-pinned, explicit FTPS staging and read-only video fetching.

No print/start/heater controls are implemented in this transport. All Bambu
device identifiers and access codes are supplied at runtime, not committed.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import socket
import ssl
import tempfile
from collections.abc import Callable
from contextlib import suppress
from ftplib import FTP, FTP_TLS, Error
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[a-f0-9]{64}$")
SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,150}$")
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_JOB_BYTES = 64 * 1024 * 1024
MAX_VIDEO_BYTES = 10 * 1024 * 1024 * 1024


class PrinterTransferError(RuntimeError):
    """Rejected or ambiguous printer SD transfer."""


class ImplicitPinnedFTPS(FTP_TLS):
    """Pin self-signed Bambu TLS cert before sending credentials."""

    def __init__(self, fingerprint: str, *, timeout: int = 20):
        if not HEX64.fullmatch(fingerprint):
            raise PrinterTransferError("operator-approved TLS fingerprint required")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        super().__init__(context=context, timeout=timeout)
        self._fingerprint = fingerprint
        self._prot_p: bool = False

    def _pin(self, sock: ssl.SSLSocket) -> None:
        cert = sock.getpeercert(binary_form=True)
        if not cert or hashlib.sha256(cert).hexdigest() != self._fingerprint:
            sock.close()
            raise PrinterTransferError("FTPS TLS certificate pin mismatch")

    def connect(
        self,
        host: str = "",
        port: int = 990,
        timeout: float = 20.0,
        source_address: tuple[str, int] | None = None,
    ) -> str:
        if port != 990:
            raise PrinterTransferError("expected Bambu implicit FTPS port 990")
        self.host = host
        self.port = port
        self.timeout = timeout or self.timeout
        raw = socket.create_connection((host, port), self.timeout, source_address)
        self.sock = self.context.wrap_socket(raw, server_hostname=host)
        self._pin(self.sock)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(
        self, cmd: str, rest: int | str | None = None
    ) -> tuple[socket.socket, int | None]:
        sock, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                secure = self.context.wrap_socket(sock, server_hostname=self.host)
                self._pin(secure)
                sock = secure
            except (OSError, ValueError, Error, PrinterTransferError):
                sock.close()
                raise
        return sock, size


class PrinterStorage:
    """FTPS is deliberately limited to stage / read / video fetch."""

    def __init__(
        self,
        *,
        hostname: str,
        fingerprint: str,
        secret_provider: Callable[[], str],
        connector: Callable[..., FTP_TLS] = ImplicitPinnedFTPS,
    ):
        if not isinstance(hostname, str) or not hostname or any(c.isspace() for c in hostname):
            raise PrinterTransferError("invalid printer hostname")
        if not HEX64.fullmatch(fingerprint):
            raise PrinterTransferError("missing pinned printer certificate")
        self.hostname = hostname
        self.fingerprint = fingerprint
        self.secret_provider = secret_provider
        self.connector = connector

    @staticmethod
    def _close(ftp: FTP_TLS) -> None:
        with suppress(OSError, EOFError, Error):
            ftp.quit()
        with suppress(OSError, EOFError, Error):
            ftp.close()

    def _connect(self) -> FTP_TLS:
        ftp = self.connector(self.fingerprint)
        try:
            ftp.connect(self.hostname, 990)
            credential = self.secret_provider()
            if not credential or not isinstance(credential, str):
                raise PrinterTransferError("printer credential missing")
            ftp.login("bblp", credential)
            ftp.prot_p()
            return ftp
        except (OSError, EOFError, ValueError, Error, PrinterTransferError):
            self._close(ftp)
            raise PrinterTransferError("FTPS connection or authorization rejected") from None

    @staticmethod
    def _name(filename: str, *, video: bool = False) -> str:
        if not isinstance(filename, str) or not SAFE.fullmatch(filename):
            raise PrinterTransferError("unsafe printer SD filename")
        if video:
            if not filename.lower().endswith((".mp4", ".avi")):
                raise PrinterTransferError("unsupported printer recording extension")
        elif not filename.endswith(".gcode.3mf"):
            raise PrinterTransferError("only sliced .gcode.3mf may be staged")
        return filename

    @staticmethod
    def _remote_hash(ftp: FTP_TLS, filename: str, bound: int) -> tuple[str, int]:
        hasher = hashlib.sha256()
        total = 0

        def receive(chunk: bytes) -> None:
            nonlocal total
            total += len(chunk)
            if total > bound:
                raise PrinterTransferError("remote file exceeds bound")
            hasher.update(chunk)

        ftp.retrbinary("RETR " + filename, receive, blocksize=128 * 1024)
        return hasher.hexdigest(), total

    @staticmethod
    def _validate_source(filename: str, source: Path, expected: str) -> tuple[int, str, bytes]:
        if not HEX64.fullmatch(expected) or source.is_symlink() or not source.is_file():
            raise PrinterTransferError("untrusted staging path or hash")
        if source.name != filename:
            raise PrinterTransferError("source/remote basename mismatch")
        size = source.stat().st_size
        if not 0 < size <= MAX_JOB_BYTES:
            raise PrinterTransferError("staged 3MF too large")
        blob = source.read_bytes()
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected or len(blob) != size:
            raise PrinterTransferError("local 3MF hash mismatch")
        return size, actual, blob

    @staticmethod
    def _upload_if_missing(ftp: FTP_TLS, filename: str, blob: bytes) -> None:
        if filename in [Path(name).name for name in ftp.nlst()]:
            return
        try:
            ftp.storbinary("STOR " + filename, io.BytesIO(blob), blocksize=128 * 1024)
        except (OSError, EOFError, Error):
            # STOR may have committed despite a timeout. VERIFY REMOTE; do not retry.
            return

    def stage_verified(
        self, filename: str, local_file: Path, expected_sha256: str
    ) -> dict[str, Any]:
        """Stage one bounded G-code plate and prove the bytes match readback."""
        self._name(filename)
        size, actual, blob = self._validate_source(filename, local_file, expected_sha256)
        ftp = self._connect()
        try:
            ftp.cwd("/model")
            self._upload_if_missing(ftp, filename, blob)
            remote_sha, remote_size = self._remote_hash(ftp, filename, MAX_JOB_BYTES)
            if remote_sha != actual or remote_size != size:
                raise PrinterTransferError("remote SD hash/size mismatch")
            return {
                "filename": filename,
                "artifact_sha256": actual,
                "remote_sha256": remote_sha,
                "readback_verified": True,
                "bytes": size,
                "remote_directory": "/model",
            }
        except PrinterTransferError:
            raise
        except (OSError, EOFError, Error):
            raise PrinterTransferError("FTPS staging uncertain; no blind retry") from None
        finally:
            self._close(ftp)

    @staticmethod
    def _video_destination(
        owner_hash: str,
        job_id: str,
        remote_folder: str,
        filename: str,
        spool_root: Path,
    ) -> Path:
        if not HEX64.fullmatch(owner_hash) or not JOB_ID.fullmatch(job_id):
            raise PrinterTransferError("invalid trusted owner/job id")
        if remote_folder not in {"/timelapse", "/recorder"}:
            raise PrinterTransferError("video directory not allowlisted")
        output_dir = spool_root / owner_hash / job_id
        if output_dir.is_symlink() or spool_root.is_symlink():
            raise PrinterTransferError("video spool symlink forbidden")
        output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination = output_dir / filename
        if destination.exists() or destination.is_symlink():
            raise PrinterTransferError("recording exists; will not overwrite")
        return destination

    @staticmethod
    def _download_video(ftp: FTP_TLS, filename: str, fd: int) -> None:
        total = 0
        with os.fdopen(fd, "wb", closefd=False) as stream:

            def write(block: bytes) -> None:
                nonlocal total
                total += len(block)
                if total > MAX_VIDEO_BYTES:
                    raise PrinterTransferError("printer recording exceeds size cap")
                stream.write(block)

            ftp.retrbinary("RETR " + filename, write, blocksize=128 * 1024)
            if total == 0:
                raise PrinterTransferError("empty printer recording")
            stream.flush()
            os.fsync(stream.fileno())

    def fetch_video(
        self,
        owner_hash: str,
        job_id: str,
        remote_folder: str,
        filename: str,
        spool_root: Path,
    ) -> Path:
        """Read a printer recording; NEVER delete it from the printer SD."""
        self._name(filename, video=True)
        destination = self._video_destination(
            owner_hash, job_id, remote_folder, filename, spool_root
        )
        ftp = self._connect()
        fd, temporary = tempfile.mkstemp(prefix=".video-", dir=destination.parent)
        try:
            ftp.cwd(remote_folder)
            self._download_video(ftp, filename, fd)
            os.close(fd)
            fd = -1
            os.link(temporary, destination, follow_symlinks=False)
            return destination
        except PrinterTransferError:
            raise
        except (OSError, EOFError, Error):
            raise PrinterTransferError("printer recording unavailable") from None
        finally:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(temporary):
                os.unlink(temporary)
            self._close(ftp)
