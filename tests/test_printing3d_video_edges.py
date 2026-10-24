"""Offline failure-path and resumable-protocol tests for Google Drive video archiving.

All Google API calls are replaced by deterministic fake responses. No uploads occur.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from printing3d import video_archive
from printing3d.video_archive import (
    ArchiveError,
    GoogleDriveResumableClient,
    MediaProof,
    VideoArchive,
    _check_video,
    _NoRedirect,
)

ROOT_FOLDER = "expectedfolder012345"
SHA = "a" * 64


class FakeResponse:
    def __init__(self, code=200, headers=None, content=b""):
        self.status = code
        self.headers = headers or {}
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, length):
        return self.content[:length]


class InputVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.owner = "trusted-owner"
        self.owner_hash = hashlib.sha256(self.owner.encode()).hexdigest()
        self.job_id = uuid.uuid4().hex
        self.folder = self.root / self.owner_hash / self.job_id
        self.folder.mkdir(parents=True)
        self.video = self.folder / "clip.mp4"
        self.video.write_bytes(b"fixture video")
        self.proof = _check_video(self.video, self.root, self.owner, self.job_id)

    def test_rejects_invalid_job_and_bad_drive_folder(self):
        with self.assertRaisesRegex(ArchiveError, "job id"):
            _check_video(self.video, self.root, self.owner, "not-a-job")
        with self.assertRaisesRegex(ArchiveError, "folder"):
            VideoArchive(self.root, Mock(), "!invalid!")

    def test_zero_or_oversized_videos_not_accepted(self):
        self.video.write_bytes(b"")
        with self.assertRaisesRegex(ArchiveError, "too large"):
            _check_video(self.video, self.root, self.owner, self.job_id)
        self.video.write_bytes(b"large-clip")
        with (
            patch.object(video_archive, "MAX_VIDEO_BYTES", 4),
            self.assertRaisesRegex(ArchiveError, "too large"),
        ):
            _check_video(self.video, self.root, self.owner, self.job_id)

    def test_video_changed_during_check(self):
        real_fstat = os.fstat
        count = 0

        def fake_fstat(fd):
            nonlocal count
            count += 1
            result = real_fstat(fd)
            if count == 2:
                return type("fake_stat", (), {"st_size": result.st_size + 1})()
            return result

        with (
            patch.object(video_archive.os, "fstat", side_effect=fake_fstat),
            self.assertRaisesRegex(ArchiveError, "changed"),
        ):
            _check_video(self.video, self.root, self.owner, self.job_id)

    def test_unknown_quota_does_not_claim_free_space(self):
        drive = Mock()
        drive.lookup.return_value = None
        drive.quota.return_value = (None, None)
        drive.upload.return_value = {
            "name": self.job_id + "_" + self.proof.sha256[:12] + "_clip.mp4",
            "size": str(self.proof.bytes),
            "md5Checksum": self.proof.md5,
            "id": "verified123",
        }
        obj = VideoArchive(self.root, drive, ROOT_FOLDER)
        self.assertEqual(
            obj.archive(self.owner, self.job_id, self.video)["drive_id"], "verified123"
        )
        drive.upload.assert_called_once()


class HttpClientTests(unittest.TestCase):
    def setUp(self):
        self.client = GoogleDriveResumableClient(lambda: "runtime-token")
        self.url = "https://www.googleapis.com/drive/v3/about"

    def test_no_redirect_handler_defers(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_request_success_propagates_headers_and_body(self):
        response = FakeResponse(200, {"X-Test": "yes"}, b'{"ok":true}')
        with patch.object(self.client._opener, "open", return_value=response) as f:
            code, headers, body = self.client._request("GET", self.url)
        self.assertEqual((code, body), (200, b'{"ok":true}'))
        self.assertEqual(headers["X-Test"], "yes")
        request = f.call_args.args[0]
        self.assertIn("Bearer runtime-token", request.headers.values())

    def test_http_308_and_other_error_are_fail_closed(self):
        for status in (308, 403):
            err = urllib.error.HTTPError(
                self.url, status, "provider error", {"Range": "bytes=0-3"}, None
            )
            with patch.object(self.client._opener, "open", side_effect=err):
                if status == 308:
                    self.assertEqual(self.client._request("PUT", self.url, b"abc")[0], 308)
                else:
                    with self.assertRaisesRegex(ArchiveError, "HTTP 403"):
                        self.client._request("PUT", self.url, b"abc")

    def test_token_or_network_failure_no_secrets_in_exception(self):
        empty = GoogleDriveResumableClient(lambda: "")
        with self.assertRaisesRegex(ArchiveError, "OAuth"):
            empty._request("GET", self.url)
        with (
            patch.object(self.client._opener, "open", side_effect=OSError("private bearer")),
            self.assertRaises(ArchiveError) as caught,
        ):
            self.client._request("GET", self.url)
        self.assertNotIn("private bearer", str(caught.exception))

    def test_quota_missing_unlimited_and_explicit_limit(self):
        for value, wanted in [
            ({}, (None, None)),
            ({"storageQuota": {"limit": "5000", "usage": "100"}}, (5000, 100)),
        ]:
            with patch.object(
                self.client, "_request", return_value=(200, {}, json.dumps(value).encode())
            ):
                self.assertEqual(self.client.quota(), wanted)
        with (
            patch.object(self.client, "_request", return_value=(403, {}, b"{}")),
            self.assertRaisesRegex(ArchiveError, "quota request"),
        ):
            self.client.quota()

    def test_listing_missing_duplicate_conflicting_and_invalid_inputs(self):
        filename = "safe_recording.mp4"
        cases = [
            ({"files": []}, None),
            ({"files": [{"id": "good", "appProperties": {"sha256": SHA}}]}, "good"),
        ]
        for body, expected in cases:
            with patch.object(
                self.client, "_request", return_value=(200, {}, json.dumps(body).encode())
            ):
                result = self.client.lookup(filename, SHA, ROOT_FOLDER)
                self.assertEqual(result["id"] if result else None, expected)
        for body in [
            {"files": [{"id": "conflict", "appProperties": {"sha256": "b" * 64}}]},
            {
                "files": [
                    {"id": "x", "appProperties": {"sha256": SHA}},
                    {"id": "y", "appProperties": {"sha256": SHA}},
                ]
            },
        ]:
            with (
                patch.object(
                    self.client, "_request", return_value=(200, {}, json.dumps(body).encode())
                ),
                self.assertRaisesRegex(ArchiveError, "ambiguous"),
            ):
                self.client.lookup(filename, SHA, ROOT_FOLDER)
        for folder, sha in [("bad", SHA), (ROOT_FOLDER, "invalid")]:
            with self.assertRaisesRegex(ArchiveError, "parent/hash"):
                self.client.lookup(filename, sha, folder)
        with (
            patch.object(self.client, "_request", return_value=(429, {}, b"{}")),
            self.assertRaisesRegex(ArchiveError, "listing"),
        ):
            self.client.lookup(filename, SHA, ROOT_FOLDER)

    def test_upload_session_check(self):
        self.assertEqual(self.client._require_session(200, {"Location": self.url}), self.url)
        for status, headers in [(200, {}), (500, {"Location": self.url})]:
            with self.assertRaisesRegex(ArchiveError, "session"):
                self.client._require_session(status, headers)


class UploadProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "video.mp4"
        self.data = b"123456789"
        self.file.write_bytes(self.data)
        self.proof = MediaProof(
            self.file.name,
            len(self.data),
            hashlib.sha256(self.data).hexdigest(),
            hashlib.md5(self.data).hexdigest(),
            "c" * 64,
            uuid.uuid4().hex,
        )
        self.session = "https://www.googleapis.com/upload/drive/v3/files?session=fixture"
        self.client = GoogleDriveResumableClient(lambda: "fixture-token")
        self.client.CHUNK_BYTES = 4
        self.name = self.proof.job_id + "_" + self.proof.sha256[:12] + "_" + self.proof.filename

    def run_upload(self, replies):
        iterator = iter(replies)

        def fake_request(method, url, data=None, headers=None):
            if method == "POST":
                self.assertIn(b"video/mp4", data)
                return (200, {"Location": self.session}, b"")
            self.assertEqual(method, "PUT")
            self.assertEqual(url, self.session)
            return next(iterator)

        with patch.object(self.client, "_request", side_effect=fake_request):
            return self.client.upload(self.file, self.proof, ROOT_FOLDER)

    def test_resumable_upload_with_verified_md5_metadata(self):
        uploaded = {
            "id": "fileID",
            "name": self.name,
            "size": str(self.proof.bytes),
            "md5Checksum": self.proof.md5,
        }
        result = self.run_upload(
            [
                (308, {"Range": "bytes=0-3"}, b""),
                (308, {"Range": "bytes=0-7"}, b""),
                (200, {}, json.dumps(uploaded).encode()),
            ]
        )
        self.assertEqual(result, uploaded)

    def test_invalid_folder_and_missing_session(self):
        with self.assertRaisesRegex(ArchiveError, "folder"):
            self.client.upload(self.file, self.proof, "!")
        with (
            patch.object(self.client, "_request", return_value=(201, {}, b"")),
            self.assertRaisesRegex(ArchiveError, "session"),
        ):
            self.client.upload(self.file, self.proof, ROOT_FOLDER)

    def test_source_modified_since_proof(self):
        with (
            patch.object(
                self.client, "_request", return_value=(200, {"Location": self.session}, b"")
            ),
            self.assertRaisesRegex(ArchiveError, "changed"),
        ):
            self.client.upload(
                self.file,
                MediaProof(
                    self.file.name,
                    99,
                    self.proof.sha256,
                    self.proof.md5,
                    self.proof.owner_hash,
                    self.proof.job_id,
                ),
                ROOT_FOLDER,
            )

    def test_short_read_detected_after_first_upload_chunk(self):
        real_fdopen = os.fdopen

        class ShortReader:
            def __init__(self, stream):
                self.stream = stream
                self.read_count = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                self.read_count += 1
                if self.read_count == 1:
                    return self.stream.read(size)
                return b""

        def fake_request(method, url, data=None, headers=None):
            if method == "POST":
                return 200, {"Location": self.session}, b""
            return 308, {"Range": "bytes=0-3"}, b""

        with (
            patch.object(self.client, "_request", side_effect=fake_request),
            patch.object(
                video_archive.os,
                "fdopen",
                side_effect=lambda fd, mode: ShortReader(real_fdopen(fd, mode)),
            ),
            self.assertRaisesRegex(ArchiveError, "ended before"),
        ):
            self.client.upload(self.file, self.proof, ROOT_FOLDER)

    def test_early_complete_non_dict_wrong_status_and_bad_range(self):
        replies = [
            [(200, {}, b"{}")],
            [
                (308, {"Range": "bytes=0-3"}, b""),
                (308, {"Range": "bytes=0-7"}, b""),
                (200, {}, b"[]"),
            ],
            [(500, {}, b"")],
            [(308, {"Range": "bytes=0-2"}, b"")],
        ]
        for seq in replies:
            with self.subTest(seq=seq), self.assertRaises(ArchiveError):
                self.run_upload(seq)

    def test_missing_completion_after_every_chunk_has_308(self):
        with self.assertRaisesRegex(ArchiveError, "without remote verification"):
            self.run_upload(
                [
                    (308, {"Range": "bytes=0-3"}, b""),
                    (308, {"Range": "bytes=0-7"}, b""),
                    (308, {"Range": "bytes=0-8"}, b""),
                ]
            )


if __name__ == "__main__":
    unittest.main()
