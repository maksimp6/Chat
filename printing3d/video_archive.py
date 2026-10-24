"""Private, quota-aware Google Drive archive for printer video files.

Video bytes never belong in a public Git repository or Execution Trace.
The OAuth token provider must come from Alice's server-side SecretProvider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MAX_VIDEO_BYTES = 10 * 1024 * 1024 * 1024
MIN_FREE_RESERVE = 512 * 1024 * 1024
VALID_VIDEO = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,150}\.(mp4|avi|mkv)$", re.IGNORECASE)
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class ArchiveError(ValueError):
    """Video storage validation, quota or integrity failure."""


@dataclass(frozen=True)
class MediaProof:
    filename: str
    bytes: int
    sha256: str
    md5: str
    owner_hash: str
    job_id: str


class DriveClient(Protocol):
    def quota(self) -> tuple[int | None, int | None]: ...
    def lookup(self, filename: str, sha256: str, parent_id: str) -> dict[str, Any] | None: ...
    def upload(self, source: Path, proof: MediaProof, parent_id: str) -> dict[str, Any]: ...


def _check_video(source: Path, root: Path, owner: str, job_id: str) -> MediaProof:
    if not JOB_ID.fullmatch(job_id):
        raise ArchiveError("invalid job id")
    filename = source.name
    if not VALID_VIDEO.fullmatch(filename):
        raise ArchiveError("only safe video recordings are supported")
    owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()
    approved = root.resolve() / owner_hash / job_id
    if source.is_symlink() or root.is_symlink() or approved.is_symlink():
        raise ArchiveError("symlink video source is forbidden")
    if source.resolve().parent != approved or not source.is_file():
        raise ArchiveError("video is outside owner-isolated spool")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source, flags)
    try:
        size = os.fstat(fd).st_size
        if size <= 0 or size > MAX_VIDEO_BYTES:
            raise ArchiveError("video is empty or too large")
        sha = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while block := stream.read(1024 * 1024):
                sha.update(block)
                md5.update(block)
        if os.fstat(fd).st_size != size:
            raise ArchiveError("video changed while validating")
        return MediaProof(filename, size, sha.hexdigest(), md5.hexdigest(), owner_hash, job_id)
    finally:
        os.close(fd)


class VideoArchive:
    """Archive verified recordings; never delete the only local copy."""

    def __init__(self, spool: Path, drive: DriveClient, folder_id: str):
        if not FOLDER_ID.fullmatch(folder_id):
            raise ArchiveError("invalid verified Google Drive folder id")
        self.spool = spool
        self.drive = drive
        self.folder_id = folder_id

    def archive(self, owner: str, job_id: str, video: Path) -> dict[str, Any]:
        proof = _check_video(video, self.spool, owner, job_id)
        remote_name = job_id + "_" + proof.sha256[:12] + "_" + proof.filename
        existing = self.drive.lookup(remote_name, proof.sha256, self.folder_id)
        if existing is not None:
            return self._verify(existing, proof, remote_name)

        limit, usage = self.drive.quota()
        if limit is not None and (usage is None or limit - usage < proof.bytes + MIN_FREE_RESERVE):
            raise ArchiveError("Google Drive quota is too low; local video preserved")
        remote = self.drive.upload(video, proof, self.folder_id)
        return self._verify(remote, proof, remote_name)

    @staticmethod
    def _verify(remote: dict[str, Any], proof: MediaProof, remote_name: str) -> dict[str, Any]:
        if (
            not isinstance(remote, dict)
            or not isinstance(remote.get("id"), str)
            or not remote["id"]
            or remote.get("name") != remote_name
            or str(remote.get("size")) != str(proof.bytes)
            or remote.get("md5Checksum") != proof.md5
        ):
            raise ArchiveError("remote video metadata mismatch; retain local recording")
        return {
            "status": "verified_upload",
            "drive_id": remote["id"],
            "filename": remote_name,
            "sha256": proof.sha256,
            "bytes": proof.bytes,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward a bearer token to a Location supplied by a server."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class GoogleDriveResumableClient:
    """Native Google Drive API v3 resumable upload using runtime OAuth.

    No token is stored; API endpoints are pinned and errors are sanitized.
    The parent folder ID is injected from trusted deployment configuration.
    """

    BASE = "https://www.googleapis.com"
    CHUNK_BYTES = 4 * 256 * 1024

    def __init__(self, token_provider: Callable[[], str]):
        self._token_provider = token_provider
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise ArchiveError("unexpected Drive API upload endpoint")
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token.strip():
                raise ArchiveError("Drive OAuth is not configured")
            request = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    **(headers or {}),
                },
            )
            try:
                with self._opener.open(request, timeout=60) as response:
                    return response.status, dict(response.headers), response.read(1024 * 1024)
            except urllib.error.HTTPError as error:
                if error.code == 308:
                    return error.code, dict(error.headers), b""
                raise ArchiveError("Drive API rejected request: HTTP " + str(error.code)) from None
        except ArchiveError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise ArchiveError("Drive network or OAuth provider unavailable") from None

    def quota(self) -> tuple[int | None, int | None]:
        status, _, raw = self._request(
            "GET", self.BASE + "/drive/v3/about?fields=storageQuota(limit%2Cusage)"
        )
        if status != 200:
            raise ArchiveError("Drive quota request failed")
        data = json.loads(raw)
        quota = data.get("storageQuota", {})
        limit = quota.get("limit")
        used = quota.get("usage")
        return (int(limit) if limit is not None else None, int(used) if used is not None else None)

    def lookup(self, filename: str, sha256: str, parent_id: str) -> dict[str, Any] | None:
        if not FOLDER_ID.fullmatch(parent_id) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ArchiveError("invalid Drive parent/hash")
        escaped = filename.replace("\\", "\\\\").replace("'", "\\'")
        query = f"'{parent_id}' in parents and name = '{escaped}' and trashed = false"
        url = (
            self.BASE
            + "/drive/v3/files?"
            + urllib.parse.urlencode(
                {
                    "q": query,
                    "fields": "files(id,name,size,md5Checksum,appProperties),nextPageToken",
                    "page_size": 20,
                }
            )
        )
        status, _, raw = self._request("GET", url)
        if status != 200:
            raise ArchiveError("Drive listing failed")
        data = json.loads(raw)
        files = data.get("files", [])
        matching = [f for f in files if f.get("appProperties", {}).get("sha256") == sha256]
        if len(matching) > 1 or (files and not matching):
            raise ArchiveError("conflicting or ambiguous Drive video metadata")
        return matching[0] if matching else None

    @staticmethod
    def _require_session(status: int, headers: dict[str, str]) -> str:
        session = headers.get("Location") or headers.get("location")
        if status not in (200, 201) or not session:
            raise ArchiveError("Drive did not return a resumable upload session")
        return session

    def upload(self, source: Path, proof: MediaProof, parent_id: str) -> dict[str, Any]:
        if not FOLDER_ID.fullmatch(parent_id):
            raise ArchiveError("invalid Drive folder")
        name = proof.job_id + "_" + proof.sha256[:12] + "_" + proof.filename
        metadata: dict[str, Any] = {
            "name": name,
            "parents": [parent_id],
            "mimeType": {
                ".mp4": "video/mp4",
                ".avi": "video/x-msvideo",
                ".mkv": "video/x-matroska",
            }[source.suffix.lower()],
            "appProperties": {
                "job_id": proof.job_id,
                "owner_hash": proof.owner_hash,
                "sha256": proof.sha256,
            },
        }
        status, headers, _ = self._request(
            "POST",
            self.BASE
            + "/upload/drive/v3/files?uploadType=resumable&fields=id,name,size,md5Checksum",
            json.dumps(metadata).encode(),
            {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": metadata["mimeType"],
                "X-Upload-Content-Length": str(proof.bytes),
            },
        )
        session = self._require_session(status, headers)
        offset = 0
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(source, flags)
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_size != proof.bytes:
                raise ArchiveError("video changed since local audit")
            while offset < proof.bytes:
                chunk = stream.read(min(self.CHUNK_BYTES, proof.bytes - offset))
                if not chunk:
                    raise ArchiveError("local video ended before expected size")
                end = offset + len(chunk) - 1
                code, response_headers, result = self._request(
                    "PUT",
                    session,
                    chunk,
                    {
                        "Content-Type": metadata["mimeType"],
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{proof.bytes}",
                    },
                )
                if code in (200, 201):
                    if end != proof.bytes - 1:
                        raise ArchiveError("Drive completed before entire recording was uploaded")
                    output = json.loads(result)
                    if not isinstance(output, dict):
                        raise ArchiveError("invalid Drive upload metadata")
                    return output
                if code != 308:
                    raise ArchiveError("Drive upload returned an unexpected status")
                received = response_headers.get("Range") or response_headers.get("range")
                if received != f"bytes=0-{end}":
                    raise ArchiveError("ambiguous resumable upload offset; do not retry blindly")
                offset = end + 1
        raise ArchiveError("video upload ended without remote verification")
