#!/usr/bin/env python3
"""Bambu A1 mini: one-shot authenticated camera snapshot (no live streaming).

Read-only: connects to the printer camera only; sends no motion/print commands.
Access code is loaded from a private file on this device, never a CLI argument.
"""
import argparse
import hashlib
import getpass
import io
import os
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
import time

HOME = Path.home() / ".alice-rdc"
CODE_FILE = HOME / "state" / "bambu-camera-code"
PIN_FILE = HOME / "state" / "bambu-camera-cert.sha256"
OUTPUT = HOME / "screenshots" / "a1-mini-latest.jpg"
DEFAULT_HOST = "192.168.31.12"
HEADER = struct.Struct("<IIII")
MAX_IMAGE_BYTES = 5_000_000

class CameraError(RuntimeError):
    pass

def auth_packet(code):
    if len(code) != 8 or not code.isascii() or not code.isalnum():
        raise CameraError("Invalid credential format")
    return HEADER.pack(0x40, 0x3000, 0, 0) + b"bblp".ljust(32, b"\0") + code.encode("ascii").ljust(32, b"\0")

def read_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        data = sock.recv(count - len(result))
        if not data:
            raise CameraError("Camera stream closed before first frame (credential or permissions may be invalid)")
        result.extend(data)
    return bytes(result)

def jpeg_from_stream(sock):
    h = read_exact(sock, HEADER.size)
    size, track, flags, reserved = HEADER.unpack(h)
    if not (1000 < size <= MAX_IMAGE_BYTES):
        raise CameraError("Invalid camera frame size")
    if track not in (0,):
        raise CameraError("Unexpected camera track")
    image = read_exact(sock, size)
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        raise CameraError("Camera sent incomplete/non-JPEG frame")
    return image

def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".camera-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def read_access_code(path):
    if not path.exists():
        raise CameraError("NO_CREDENTIAL: enter a fresh printer LAN access code in the private credential file")
    if path.stat().st_mode & 0o077:
        raise CameraError("Credential file is not private (requires mode 0600)")
    return path.read_text(encoding="ascii").strip()

def snapshot(host, output, code_file, pin_file):
    code = read_access_code(code_file)
    # Printer ships with its own certificate; pin the certificate locally.
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 6000), timeout=5) as connection:
        with tls.wrap_socket(connection, server_hostname=host) as stream:
            stream.settimeout(15)
            cert = stream.getpeercert(binary_form=True)
            if not cert:
                raise CameraError("No TLS certificate")
            fingerprint = hashlib.sha256(cert).hexdigest()
            if pin_file.exists():
                if fingerprint != pin_file.read_text().strip():
                    raise CameraError("Printer TLS certificate changed; connection refused")
            else:
                private_write(pin_file, (fingerprint + "\n").encode("ascii"))
            stream.sendall(auth_packet(code))
            jpg = jpeg_from_stream(stream)
    private_write(output, jpg)
    return len(jpg)

def self_test():
    sample = b"\xff\xd8" + b"0" * 2000 + b"\xff\xd9"
    data = HEADER.pack(len(sample), 0, 1, 0) + sample
    assert len(auth_packet("12345678")) == 80
    class FakeSocket:
        def __init__(self, payload):
            self.source = io.BytesIO(payload)
        def recv(self, size):
            return self.source.read(size)
    assert jpeg_from_stream(FakeSocket(data)) == sample
    try:
        jpeg_from_stream(FakeSocket(HEADER.pack(MAX_IMAGE_BYTES + 1, 0, 1, 0)))
        raise AssertionError("image size guard failed")
    except CameraError:
        pass
    print("SELFTEST_OK: header, JPEG and size checks")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--pair", action="store_true", help="Enter camera code privately on this device")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        elif args.pair:
            code = getpass.getpass("Printer access code (not displayed): ").strip()
            auth_packet(code)
            private_write(CODE_FILE, (code + "\n").encode("ascii"))
            print("PAIRING_CODE_SAVED_LOCAL_ONLY")
        else:
            length = snapshot(args.host, OUTPUT, CODE_FILE, PIN_FILE)
            print(f"SNAPSHOT_OK bytes={length} path={OUTPUT}")
    except (CameraError, OSError, ssl.SSLError) as exc:
        print(f"SNAPSHOT_UNAVAILABLE: {exc}", file=sys.stderr)
        sys.exit(2)
