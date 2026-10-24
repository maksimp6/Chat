"""Offline positive/negative FTPS transport tests; no sockets opened."""

from __future__ import annotations

import hashlib
import io
import os
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from printing3d import ftps_storage
from printing3d.ftps_storage import (
    ImplicitPinnedFTPS,
    PrinterStorage,
    PrinterTransferError,
)

CERT = b"fake-certificate-der"
PIN = hashlib.sha256(CERT).hexdigest()
NAME = "part_A1mini.gcode.3mf"
OWNER = "a" * 64


class DummySocket:
    family = socket.AF_INET

    def __init__(self, cert=CERT):
        self.cert = cert
        self.closed = False

    def getpeercert(self, *, binary_form=False):
        return self.cert

    def makefile(self, *args, **kwargs):
        return io.StringIO("220 Hello\n")

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls = 0

    def wrap_socket(self, sock, *, server_hostname=None):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or DummySocket()


class FakeFTP:
    def __init__(self):
        self.fs = {}
        self.fail = None
        self.command_log = []
        self.close_called = 0

    def connect(self, *args):
        if self.fail == "connect":
            raise OSError("offline")

    def login(self, user, credential):
        if self.fail == "login":
            raise OSError("unauthorized")

    def prot_p(self):
        pass

    def cwd(self, folder):
        if self.fail == "cwd":
            raise OSError("network")

    def nlst(self):
        return list(self.fs)

    def storbinary(self, command, file, blocksize=1024):
        self.command_log.append("STOR")
        self.fs[command.split(" ", 1)[1]] = file.read()
        if self.fail == "stor":
            raise OSError("late STOR timeout")

    def retrbinary(self, command, callback, blocksize=1024):
        self.command_log.append("RETR")
        if self.fail == "retr":
            raise OSError("late RETR timeout")
        callback(self.fs[command.split(" ", 1)[1]])

    def quit(self):
        self.close_called += 1

    def close(self):
        self.close_called += 1


class FTPSCertificateTests(unittest.TestCase):
    def test_validation_and_pin_both_paths(self):
        with self.assertRaises(PrinterTransferError):
            ImplicitPinnedFTPS("wrong")
        c = ImplicitPinnedFTPS(PIN)
        c._pin(DummySocket())
        missing = DummySocket(b"")
        with self.assertRaises(PrinterTransferError):
            c._pin(missing)
        self.assertTrue(missing.closed)
        wrong = DummySocket(b"attacker-cert")
        with self.assertRaises(PrinterTransferError):
            c._pin(wrong)
        self.assertTrue(wrong.closed)

    def test_implicit_tls_handshake_is_pinned_before_welcome(self):
        ftps = ImplicitPinnedFTPS(PIN)
        ftps.context = FakeContext(DummySocket())
        with (
            patch("printing3d.ftps_storage.socket.create_connection", return_value=DummySocket()),
            patch.object(ftps, "getresp", return_value="220 hello"),
        ):
            self.assertEqual(ftps.connect("device.local", 990), "220 hello")
            self.assertEqual(ftps.context.calls, 1)
        with self.assertRaises(PrinterTransferError):
            ftps.connect("device.local", 21)

    def test_data_socket_pinned_on_private_transfers(self):
        for private in (False, True):
            c = ImplicitPinnedFTPS(PIN)
            c.host = "printer.local"
            c.context = FakeContext(DummySocket())
            c._prot_p = private
            raw = DummySocket()
            with patch.object(ftps_storage.FTP, "ntransfercmd", return_value=(raw, 77)):
                sock, size = c.ntransfercmd("RETR permitted", None)
            self.assertEqual(size, 77)
            self.assertEqual(c.context.calls, int(private))
            self.assertIsNotNone(sock)
        c._prot_p = True
        bad = DummySocket()
        c.context = FakeContext(OSError("ssl-failure"))
        with (
            patch.object(ftps_storage.FTP, "ntransfercmd", return_value=(bad, 2)),
            self.assertRaises(OSError),
        ):
            c.ntransfercmd("RETR permitted")
        self.assertTrue(bad.closed)

    def test_host_and_credentials_guard(self):
        for host, pin in [("", PIN), ("bad host", PIN), ("printer.local", "bad")]:
            with self.assertRaises(PrinterTransferError):
                PrinterStorage(hostname=host, fingerprint=pin, secret_provider=lambda: "pwd")
        for credential, should_fail in [("", True), ("secret", False)]:
            fake = FakeFTP()
            service = PrinterStorage(
                hostname="printer.local",
                fingerprint=PIN,
                secret_provider=lambda credential=credential: credential,
                connector=lambda fp, fake=fake: fake,
            )
            if should_fail:
                with self.assertRaises(PrinterTransferError):
                    service._connect()
                self.assertGreater(fake.close_called, 0)
            else:
                self.assertIs(service._connect(), fake)
        fake = FakeFTP()
        fake.fail = "login"
        service = PrinterStorage(
            hostname="printer.local",
            fingerprint=PIN,
            secret_provider=lambda: "secret",
            connector=lambda fp, fake=fake: fake,
        )
        with self.assertRaises(PrinterTransferError):
            service._connect()

    def test_close_is_idempotent_despite_failure(self):
        fake = Mock()
        fake.quit.side_effect = OSError("quit rejected")
        fake.close.side_effect = OSError("close rejected")
        PrinterStorage._close(fake)
        self.assertEqual(fake.quit.call_count, 1)
        self.assertEqual(fake.close.call_count, 1)


class FTPSNegativeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.file = self.root / NAME
        self.file.write_bytes(b"verified-slice-fixture")
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.ftp = FakeFTP()
        self.svc = PrinterStorage(
            hostname="printer.local",
            fingerprint=PIN,
            secret_provider=lambda: "secret",
            connector=lambda fp: self.ftp,
        )

    def test_safe_name_validation_and_video_extension(self):
        with self.assertRaises(PrinterTransferError):
            self.svc._name("photo.jpg", video=True)
        with self.assertRaises(PrinterTransferError):
            self.svc._name("bad.png")
        self.assertEqual(self.svc._name("abc.mp4", video=True), "abc.mp4")
        self.assertEqual(self.svc._name("abc.avi", video=True), "abc.avi")

    def test_remote_bounded_read(self):
        self.ftp.fs = {NAME: b"long-data"}
        with self.assertRaises(PrinterTransferError):
            self.svc._remote_hash(self.ftp, NAME, 5)

    def test_local_staging_rejects_source_mismatch_limits_and_hash(self):
        other = self.root / "different.gcode.3mf"
        other.write_bytes(b"data")
        for filename, source, sha in [
            (NAME, self.file, "bad"),
            (NAME, other, self.sha),
            (NAME, self.file, "0" * 64),
        ]:
            with (
                self.subTest(name=filename, src=source.name),
                self.assertRaises(PrinterTransferError),
            ):
                self.svc.stage_verified(filename, source, sha)
        with (
            patch.object(ftps_storage, "MAX_JOB_BYTES", 2),
            self.assertRaises(PrinterTransferError),
        ):
            self.svc.stage_verified(NAME, self.file, self.sha)
        missing = self.root / "missing.gcode.3mf"
        with self.assertRaises(PrinterTransferError):
            self.svc.stage_verified(NAME, missing, self.sha)

    def test_ambiguous_stor_readback_missing_and_cwd_failure(self):
        self.ftp.fail = "cwd"
        with self.assertRaises(PrinterTransferError):
            self.svc.stage_verified(NAME, self.file, self.sha)
        self.ftp.fail = "stor"
        result = self.svc.stage_verified(NAME, self.file, self.sha)
        self.assertTrue(result["readback_verified"])
        self.assertEqual(self.ftp.command_log.count("STOR"), 1)
        self.ftp.fail = "retr"
        with self.assertRaises(PrinterTransferError):
            self.svc.stage_verified(NAME, self.file, self.sha)

    def test_video_rejects_owner_path_duplicate_and_symlink(self):
        job = uuid.uuid4().hex
        for owner, folder in [("invalid", "/timelapse"), (OWNER, "/private")]:
            with self.assertRaises(PrinterTransferError):
                self.svc.fetch_video(owner, job, folder, "film.mp4", self.root)
        dest = self.root / OWNER / job
        dest.mkdir(parents=True)
        (dest / "film.mp4").write_bytes(b"existing")
        with self.assertRaises(PrinterTransferError):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)
        (dest / "film.mp4").unlink()
        dest.rmdir()
        dest.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PrinterTransferError):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)

    def test_empty_and_oversized_video_download_and_network(self):
        job = uuid.uuid4().hex
        self.ftp.fs["film.mp4"] = b""
        with self.assertRaises(PrinterTransferError):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)
        self.ftp.fs["film.mp4"] = b"video bigger than 3 bytes"
        with (
            patch.object(ftps_storage, "MAX_VIDEO_BYTES", 3),
            self.assertRaises(PrinterTransferError),
        ):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)
        self.ftp.fail = "retr"
        with self.assertRaises(PrinterTransferError):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)
        # The failed downloads must not leave a pretend successful file.
        self.assertFalse((self.root / OWNER / job / "film.mp4").exists())

    def test_verified_video_copy_survives_source_temp_already_removed(self):
        self.ftp.fs["film.mp4"] = b"valid-clip"
        real_link = os.link

        def link_then_unlink(source, target, *, follow_symlinks=False):
            real_link(source, target, follow_symlinks=follow_symlinks)
            os.unlink(source)

        with patch.object(ftps_storage.os, "link", side_effect=link_then_unlink):
            final = self.svc.fetch_video(
                OWNER, uuid.uuid4().hex, "/recorder", "film.mp4", self.root
            )
        self.assertEqual(final.read_bytes(), b"valid-clip")

    def test_fd_cleanup_on_printer_transfer_error(self):
        job = uuid.uuid4().hex
        self.ftp.fs["film.mp4"] = b"some-data"
        with (
            patch.object(
                ftps_storage.PrinterStorage,
                "_download_video",
                side_effect=PrinterTransferError("fake read failure"),
            ),
            self.assertRaises(PrinterTransferError),
        ):
            self.svc.fetch_video(OWNER, job, "/timelapse", "film.mp4", self.root)
        self.assertEqual(list((self.root / OWNER / job).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
