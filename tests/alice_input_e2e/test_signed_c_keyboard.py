"""Signed Unix input -> Python verifier -> actual C keyboard core (mocked events).

Runs only in a combined checkout of Security #1090 and Keyboard #1091.
No root privileges, uinput device or Android input is created.
"""
import ctypes
from contextlib import ExitStack
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
C_DRIVER = ROOT / "alice_mouse/drivers/keyboard/keyboard.c"
C_HEADER = C_DRIVER.with_name("keyboard.h")
SECURITY = ROOT / "alice_mouse/security/grants.py"


def test_signed_socket_to_c_keyboard_chord_and_fault():
    assert C_DRIVER.is_file() and C_HEADER.is_file() and SECURITY.is_file(), (
        "Requires both #1090 and #1091 in integration checkout"
    )
    from alice_mouse.security import (
        AuthenticatedPrincipal, SessionAuthority, TrustedSigner,
        InputGrant, ProtectedVerifier,
    )
    from alice_mouse.security.socket_boundary import SignedInputSocket

    compiler = shutil.which("clang") or shutil.which("cc")
    assert compiler
    with tempfile.TemporaryDirectory(prefix="alice-input-c-e2e-") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        library = root / "libkeyboard.so"
        subprocess.run(
            [compiler, "-shared", "-fPIC", "-std=gnu11", "-pthread",
             "-Wall", "-Wextra", "-Werror", "-I", str(C_HEADER.parent),
             str(C_DRIVER), str(Path(__file__).with_name("keyboard_fixture.c")),
             "-o", str(library)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        lib = ctypes.CDLL(str(library))
        emit_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_ushort, ctypes.c_ushort, ctypes.c_int)
        clock_t = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p)
        events = []
        fail_up = [False]

        @emit_t
        def emit(_ctx, typ, code, value):
            events.append((typ, code, value))
            if fail_up[0] and typ == 1 and value == 0:
                return -1
            return 0

        @clock_t
        def clock(_ctx):
            return time.monotonic_ns() // 1_000_000

        lib.alice_fixture_new.argtypes = [ctypes.c_void_p, emit_t, clock_t]
        lib.alice_fixture_new.restype = ctypes.c_void_p
        lib.alice_fixture_down.argtypes = [ctypes.c_void_p, ctypes.c_ushort]
        lib.alice_fixture_down.restype = ctypes.c_int
        lib.alice_fixture_up.argtypes = [ctypes.c_void_p, ctypes.c_ushort]
        lib.alice_fixture_up.restype = ctypes.c_int
        lib.alice_fixture_close.argtypes = [ctypes.c_void_p]
        lib.alice_fixture_close.restype = ctypes.c_int
        lib.alice_fixture_free.argtypes = [ctypes.c_void_p]
        lib.alice_fixture_free.restype = None
        keyboard = lib.alice_fixture_new(None, emit, clock)
        assert keyboard, "C fixture allocation failed"

        with ExitStack() as stack:
            stack.callback(lib.alice_fixture_free, keyboard)
            stack.callback(lib.alice_fixture_close, keyboard)
            principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
            authority = SessionAuthority("owner", "redmi9",
                                         identity_verifier=lambda p: p is principal)
            authority.authorize(principal)
            key = secrets.token_bytes(32)
            signer = TrustedSigner(authority, key)

            def dispatch(action, payload):
                code = payload["key"]
                if action == "key_down":
                    return lib.alice_fixture_down(keyboard, code) == 0
                if action == "key_up":
                    return lib.alice_fixture_up(keyboard, code) == 0
                return False

            verifier = ProtectedVerifier(key, "owner", "redmi9", dispatch=dispatch)
            verifier.provision(authority, principal)
            endpoint = SignedInputSocket(root / "input.sock", verifier,
                                         peer_uid=os.getuid())
            endpoint.start()
            stack.callback(endpoint.stop)
            def send(packet):
                with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                    client.settimeout(2)
                    client.connect(str(endpoint.path))
                    client.sendall(packet)
                    return client.recv(64)
            assert send(b"key_down 30") == b"DENIED\n"
            for action, code in [
                ("key_down", 29), ("key_down", 30),
                ("key_up", 30), ("key_up", 29),
            ]:
                packet = signer.sign(principal, InputGrant(action, {"key": code}))
                assert send(packet) == b"OK\n"
            assert [e for e in events if e[0] == 1] == [
                (1, 29, 1), (1, 30, 1), (1, 30, 0), (1, 29, 0)
            ]
            fail_up[0] = True
            assert send(signer.sign(principal, InputGrant(
                "key_down", {"key": 30}))) == b"OK\n"
            assert send(signer.sign(principal, InputGrant(
                "key_up", {"key": 30}))) == b"DENIED\n"
            assert verifier.session is None
