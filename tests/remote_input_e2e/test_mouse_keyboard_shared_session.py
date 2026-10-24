"""Composite signed Mouse + real C Keyboard test without privileged input.

Requires the independent Security, Keyboard and Mouse branches in a scratch
checkout. Events are captured in test memory; nothing opens /dev/uinput.
"""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "remote_input/drivers/keyboard/keyboard.c"
FIXTURE = Path(__file__).with_name("keyboard_fixture.c")
SECURITY = ROOT / "remote_input/security/grants.py"
MOUSE = ROOT / "remote_input/core.py"


def test_shared_signed_session_mouse_and_c_keyboard(tmp_path):
    assert DRIVER.is_file() and SECURITY.is_file() and MOUSE.is_file(), (
        "Integration requires canonical Security, Keyboard and Mouse sources"
    )
    from remote_input import Command, MouseModule, RecordingBackend
    from remote_input.security import (
        AuthenticatedPrincipal,
        GrantError,
        InputGrant,
        ProtectedVerifier,
        SessionAuthority,
        TrustedSigner,
    )
    from remote_input.security.socket_boundary import SignedInputSocket

    compiler = shutil.which("clang") or shutil.which("cc")
    assert compiler, "C compiler required for isolated keyboard ABI test"
    os.chmod(tmp_path, 0o700)
    library = tmp_path / "keyboard-fixture.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-std=gnu11",
            "-pthread",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(DRIVER.parent),
            str(DRIVER),
            str(FIXTURE),
            "-o",
            str(library),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    lib = ctypes.CDLL(str(library))
    emit_t = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_ushort, ctypes.c_int
    )
    clock_t = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.c_void_p)
    events = []

    @emit_t
    def emit(_ctx, typ, code, value):
        events.append((typ, code, value))
        return 0

    @clock_t
    def clock(_ctx):
        return time.monotonic_ns() // 1_000_000

    lib.remote_input_fixture_new.argtypes = [ctypes.c_void_p, emit_t, clock_t]
    lib.remote_input_fixture_new.restype = ctypes.c_void_p
    for method in ("remote_input_fixture_down", "remote_input_fixture_up"):
        getattr(lib, method).argtypes = [ctypes.c_void_p, ctypes.c_ushort]
        getattr(lib, method).restype = ctypes.c_int
    lib.remote_input_fixture_close.argtypes = [ctypes.c_void_p]
    lib.remote_input_fixture_close.restype = ctypes.c_int
    lib.remote_input_fixture_free.argtypes = [ctypes.c_void_p]
    lib.remote_input_fixture_free.restype = None
    for name in (
        "remote_input_fixture_live_count",
        "remote_input_fixture_close_count",
        "remote_input_fixture_free_count",
    ):
        getattr(lib, name).restype = ctypes.c_int

    keyboard = lib.remote_input_fixture_new(None, emit, clock)
    assert keyboard
    with ExitStack() as stack:
        # Register C cleanup before constructing any other test-side state.
        stack.callback(lib.remote_input_fixture_free, keyboard)
        stack.callback(lib.remote_input_fixture_close, keyboard)
        focus = [True]
        mouse_backend = RecordingBackend()
        mouse = MouseModule(mouse_backend, focus_ok=lambda: focus[0])
        mouse.start(authorized=True, key=secrets.token_bytes(32))
        stack.callback(mouse.close)

        principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
        authority = SessionAuthority(
            "owner", "redmi9", identity_verifier=lambda user: user is principal
        )
        authority.authorize(principal)
        key = secrets.token_bytes(32)
        signer = TrustedSigner(authority, key)

        def dispatch(action, payload):
            if action in ("key_down", "key_up"):
                method = lib.remote_input_fixture_down if action == "key_down" else lib.remote_input_fixture_up
                return method(keyboard, payload["key"]) == 0
            if action == "move":
                # Only invoked AFTER ProtectedVerifier checks the outer grant.
                command = Command("move", payload["x"], payload["y"])
            elif action in ("down", "up"):
                command = Command(action, button=payload["button"])
            else:
                return False
            mouse.dispatch(mouse.issue(command, authorized=True))
            return True

        verifier = ProtectedVerifier(key, "owner", "redmi9", dispatch=dispatch)
        verifier.provision(authority, principal)
        # Android's sockaddr_un name limit is too short for pytest's long path.
        socket_root = Path(tempfile.mkdtemp(prefix="ai-", dir=os.environ.get("TMPDIR")))
        os.chmod(socket_root, 0o700)
        stack.callback(shutil.rmtree, socket_root)
        endpoint = SignedInputSocket(socket_root / "input.sock", verifier, peer_uid=os.getuid())
        endpoint.start()
        stack.callback(endpoint.stop)

        def send(packet):
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                client.settimeout(3)
                client.connect(str(endpoint.path))
                client.sendall(packet)
                return client.recv(64)

        assert send(b"move 3 2") == b"DENIED\n"
        viewer = AuthenticatedPrincipal("owner", "redmi9", "viewer")
        with pytest.raises(GrantError):
            signer.sign(viewer, InputGrant("move", {"x": 1, "y": 1}))
        packet = signer.sign(principal, InputGrant("move", {"x": 5, "y": -3}))
        assert send(packet) == b"OK\n"
        assert mouse_backend.events == [("move", 5, -3, 1)]

        down_key = signer.sign(principal, InputGrant("key_down", {"key": 30}))
        assert send(down_key) == b"OK\n"
        assert send(packet) == b"DENIED\n"
        assert send(down_key) == b"DENIED\n"
        assert send(signer.sign(principal, InputGrant("key_up", {"key": 30}))) == b"OK\n"
        assert [event for event in events if event[0] == 1] == [(1, 30, 1), (1, 30, 0)]
        assert send(signer.sign(principal, InputGrant("down", {"button": 1}))) == b"OK\n"
        assert mouse.held == {1}

        focus[0] = False
        assert send(signer.sign(principal, InputGrant("move", {"x": 2, "y": 1}))) == b"DENIED\n"
        assert mouse.held == set()
        assert [event[0] for event in mouse_backend.events] == ["move", "down", "up"]
        assert verifier.session is None
        assert send(signer.sign(principal, InputGrant("key_down", {"key": 30}))) == b"DENIED\n"

    assert lib.remote_input_fixture_live_count() == 0
    assert lib.remote_input_fixture_close_count() == 1
    assert lib.remote_input_fixture_free_count() == 1
