"""Build an isolated C verifier fixture for all Mouse stage tests.

Production input devices, privileged sockets, and the live RDC are not touched.
The compiled executable is temporary and never checked in as an artifact.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_c_verifier(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    compiler = shutil.which("clang") or shutil.which("cc")
    if compiler is None:
        raise RuntimeError("C compiler required for Alice Mouse verifier tests")
    source = Path(__file__).with_name("alice_mouse_c_vertical_v1.c")
    binary = tmp_path_factory.mktemp("alice-mouse-verifier") / "verifier"
    subprocess.run(
        [compiler, "-Wall", "-Wextra", "-O2", str(source), "-o", str(binary), "-lcrypto"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    module = importlib.import_module("alice_mouse_http_c_vertical_v2")
    original = module.BINARY
    module.BINARY = binary
    try:
        yield
    finally:
        module.BINARY = original
        binary.unlink(missing_ok=True)
