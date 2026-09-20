from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from deploy.preview.ssh_setup import candidate_values, validate_private_key


def generate_key(tmp_path: Path) -> str:
    key_path = tmp_path / "key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return key_path.read_text(encoding="utf-8")


def test_candidate_values_support_literal_newlines(tmp_path: Path) -> None:
    key = generate_key(tmp_path)
    escaped = key.replace("\n", "\\n")
    candidates = candidate_values(escaped)
    assert key in candidates
    assert any(validate_private_key(value) for value in candidates)


def test_candidate_values_support_double_escaped_newlines(tmp_path: Path) -> None:
    key = generate_key(tmp_path)
    escaped = key.replace("\n", "\\\\n")
    candidates = candidate_values(escaped)
    assert any(validate_private_key(value) for value in candidates)


def test_candidate_values_support_base64(tmp_path: Path) -> None:
    key = generate_key(tmp_path)
    encoded = base64.b64encode(key.encode("utf-8")).decode("ascii")
    candidates = candidate_values(encoded)
    assert key in candidates
    assert any(validate_private_key(value) for value in candidates)
