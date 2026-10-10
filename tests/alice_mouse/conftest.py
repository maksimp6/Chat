"""Build Alice Mouse's harmless C fixture once per isolated test workspace."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "alice_mouse_test_fixture.c"


def build_fixture(directory: Path) -> Path:
    """Serialize publication; build to a unique path and atomically replace."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "alice_mouse_test_fixture"
    with (directory / ".fixture-build.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.NamedTemporaryFile(prefix=".alice-fixture-", dir=directory, delete=False) as temp:
            candidate = Path(temp.name)
        try:
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-O2",
                    str(SOURCE),
                    "-o",
                    str(candidate),
                ],
                check=True,
                timeout=30,
            )
            os.chmod(candidate, 0o700)
            os.replace(candidate, target)
        finally:
            candidate.unlink(missing_ok=True)
    return target


@pytest.fixture(scope="session", autouse=True)
def alice_mouse_c_fixture(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Build only for jobs collecting process-switch tests; never share binaries."""
    if not any(Path(item.path).name == "test_alice_mouse_process_switch.py" for item in request.session.items):
        return
    output = tmp_path_factory.mktemp("alice-mouse-fixture")
    build_fixture(output)
    os.environ["ALICE_MOUSE_TEST_FIXTURE"] = str(output / "alice_mouse_test_fixture")
    try:
        yield
    finally:
        os.environ.pop("ALICE_MOUSE_TEST_FIXTURE", None)
