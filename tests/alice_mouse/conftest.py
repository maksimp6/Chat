"""Build the harmless Alice Mouse C fixture for pytest collection.

The dedicated unittest workflow builds this fixture explicitly. Generic
application and PostgreSQL pytest jobs also collect these tests, so they
must have the same prerequisite instead of failing during setUp.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "alice_mouse_test_fixture.c"
BINARY = HERE / "alice_mouse_test_fixture"


@pytest.fixture(scope="session", autouse=True)
def alice_mouse_c_fixture(request: pytest.FixtureRequest) -> None:
    """Compile only if this pytest invocation collected process-switch tests."""
    selected = any(
        Path(item.path).name == "test_alice_mouse_process_switch.py"
        for item in request.session.items
    )
    if not selected:
        return
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O2",
            str(SOURCE), "-o", str(BINARY),
        ],
        check=True,
        timeout=30,
    )
