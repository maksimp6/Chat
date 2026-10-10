"""Cross-language keycode parity contract for combined Alice Input checkout.

Requires Security #1090 and Keyboard #1091 to be integrated in this checkout.
No Android device, root permissions, or actual input events are used.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "alice_mouse/drivers/keyboard/keyboard.c"
HEADER = ROOT / "alice_mouse/drivers/keyboard/keyboard.h"
GRANTS = ROOT / "alice_mouse/security/grants.py"


def test_signed_verifier_and_c_keyboard_have_identical_keycode_policy():
    assert DRIVER.is_file(), "Keyboard #1091 must be integrated before parity gate"
    assert HEADER.is_file(), "Keyboard header missing from integrated checkout"
    assert GRANTS.is_file(), "Security #1090 must be integrated before parity gate"
    compiler = shutil.which("clang") or shutil.which("cc")
    assert compiler, "C compiler required for integration parity gate"

    from alice_mouse.security.grants import GrantError, _validate_action_payload

    with tempfile.TemporaryDirectory(prefix="alice-key-parity-") as directory:
        probe = Path(directory) / "keycodes.c"
        executable = Path(directory) / "keycodes"
        probe.write_text(
            '#include "keyboard.h"\n'
            '#include <stdio.h>\n'
            'int main(void){for(int k=0;k<=KEY_MAX;k++)'
            'if(alice_keyboard_allowed((unsigned short)k))'
            'printf("%d\\n",k);return 0;}\n',
            encoding="utf-8",
        )
        subprocess.run(
            [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-pthread", "-I", str(HEADER.parent),
             str(DRIVER), str(probe), "-o", str(executable)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            [str(executable)], check=True, capture_output=True,
            text=True, timeout=10,
        )
        physical = {int(value) for value in result.stdout.splitlines()}

    signed = set()
    for key in range(0, 768):
        try:
            _validate_action_payload("key_down", {"key": key})
            _validate_action_payload("key_up", {"key": key})
        except GrantError:
            continue
        signed.add(key)

    assert signed == physical, (
        f"Security permits unsupported C keys: {sorted(signed - physical)}; "
        f"C keyboard supports keys blocked by Security: {sorted(physical - signed)}"
    )
    assert 116 not in signed, "KEY_POWER must never be exposed"
    assert {29, 30, 42, 54, 87, 88}.issubset(signed)
