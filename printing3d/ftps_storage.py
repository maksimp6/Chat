"""Certificate-pinned, explicit FTPS staging and read-only video fetching.

No print/start/heater controls are implemented in this transport. All Bambu
device identifiers and access codes are supplied at runtime, not committed.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import ssl
import tempfile
from ftplib import FTP, FTP_TLS
from pathlib import Path
from typing import Callable

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

    def _pin(self, sock: ssl.SSLSocket) -> None:
        cert = sock.getpeercert(binary_form=True)
        if not cert or hashlib.sha256(cert).hexdigest() != self._fingerprint:
            sock.close()
            raise PrinterTransferError("FTPS TLS certificate pin mismatch")

    def connect(
        self,
        host: str = "",
        port: int = 990,
        timeout: int | None = None,
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

    def ntransfercmd(self, cmd: str, rest: str | None = None):
        sock, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                secure = self.context.wrap_socket(sock, server_hostname=self.host)
                self._pin(secure)
                sock = secure
            except Exception:
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
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass
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

    def stage_verified(self, filename: str, local_file: Path, expected_sha256: str) -> dict:
        """Never auto-retry an uncertain STOR; verify readback first."""
        self._name(filename)
        if not HEX64.fullmatch(expected_sha256) or local_file.is_symlink() or not local_file.is_file():
            raise PrinterTransferError("untrusted staging path or hash")
        if local_file.name != filename:
            raise PrinterTransferError("source/remote basename mismatch")
        size = local_file.stat().st_size
        if not 0 < size <= MAX_JOB_BYTES:
            raise PrinterTransferError("staged 3MF too large")
        actual = hashlib.sha256(local_file.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise PrinterTransferError("local 3MF hash mismatch")
        ftp = self._connect()
        try:
            ftp.cwd("/model")
            exists = filename in [Path(n).name for n in ftp.nlst()]
            if not exists:
                transfer_error = False
                try:
                    with local_file.open("rb") as src:
                        ftp.storbinary("STOR " + filename, src, blocksize=128 * 1024)
                except Exception:
                    transfer_error = True
                # Even a timeout may follow a successful STOR. Read back exactly once.
                try:
                    remote_sha, remote_size = self._remote_hash(ftp, filename, MAX_JOB_BYTES)
                except Exception:
                    raise PrinterTransferError("upload uncertain; do not blindly retry") from None
                if transfer_error and (remote_sha != actual or remote_size != size):
                    raise PrinterTransferError("upload was interrupted; no automatic retry")
            else:
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
        except Exception:
            raise PrinterTransferError("FTPS staging blocked; printer state unchanged") from None
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def fetch_video(
        self,
        owner_hash: str,
        job_id: str,
        remote_folder: str,
        filename: str,
        spool_root: Path,
    ) -> Path:
        """Copy a recording; NEVER delete it from the printer SD."""
        self._name(filename, video=True)
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
        ftp = self._connect()
        fd, tmp = tempfile.mkstemp(prefix=".video-", dir=output_dir)
        try:
            ftp.cwd(remote_folder)
            total = 0

            def write(block: bytes) -> None:
                nonlocal total
                total += len(block)
                if total > MAX_VIDEO_BYTES:
                    raise PrinterTransferError("printer recording exceeds size cap")
                os.write(fd, block)

            ftp.retrbinary("RETR " + filename, write, blocksize=128 * 1024)
            if total == 0:
                raise PrinterTransferError("empty printer recording")
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp, destination)
            return destination
        except PrinterTransferError:
            raise
        except Exception:
            raise PrinterTransferError("printer recording unavailable; retry after inspection") from None
        finally:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            try:
                ftp.quit()
            except Exception:
                ftp.close()
