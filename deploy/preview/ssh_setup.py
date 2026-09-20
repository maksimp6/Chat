#!/usr/bin/env python3
"""Normalize GitHub SSH secrets and install a valid OpenSSH key pair."""

from __future__ import annotations

import ast
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path


def _add_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _decode_escaped(value: str) -> str:
    current = value.strip()
    for _ in range(4):
        changed = False

        if len(current) >= 2 and current[0] == current[-1] and current[0] in {"'", '"'}:
            try:
                parsed = ast.literal_eval(current)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, str):
                current = parsed.strip()
                changed = True

        for old, new in (
            ("\\\\r\\\\n", "\r\n"),
            ("\\\\n", "\n"),
            ("\\\\r", "\r"),
            ("\\r\\n", "\r\n"),
            ("\\n", "\n"),
            ("\\r", "\r"),
        ):
            replaced = current.replace(old, new)
            if replaced != current:
                current = replaced
                changed = True

        try:
            parsed_json = json.loads(current)
        except (json.JSONDecodeError, TypeError):
            parsed_json = None
        if isinstance(parsed_json, str) and parsed_json != current:
            current = parsed_json.strip()
            changed = True

        if not changed:
            break

    return current.lstrip("\ufeff").strip().replace("\r\n", "\n").replace("\r", "\n")


def candidate_values(raw: str) -> list[str]:
    candidates: list[str] = []
    normalized = _decode_escaped(raw)
    _add_unique(candidates, normalized)

    compact = "".join(normalized.split())
    if compact:
        padded = compact + "=" * ((4 - len(compact) % 4) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        _add_unique(candidates, _decode_escaped(decoded))

    return [value for value in candidates if "-----BEGIN" in value]


def validate_private_key(value: str) -> bool:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as handle:
        handle.write(value)
        temp_path = handle.name
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", temp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return result.returncode == 0
    finally:
        Path(temp_path).unlink(missing_ok=True)


def install_private_key(raw: str, path: Path) -> None:
    candidates = candidate_values(raw)
    for candidate in candidates:
        if validate_private_key(candidate):
            path.parent.mkdir(parents=True, exist_ok=True)
            normalized = candidate.lstrip("\ufeff").strip() + "\n"\n            path.write_text(normalized, encoding="utf-8", newline="\n")
            os.chmod(path, 0o600)
            return
    raise RuntimeError(f"SSH private key could not be parsed from {len(candidates)} candidate format(s)")


def install_known_hosts(raw: str, path: Path) -> None:
    candidates = candidate_values(raw)
    value = candidates[0] if candidates else _decode_escaped(raw)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    os.chmod(path, 0o600)


def main() -> None:
    ssh_dir = Path.home() / ".ssh"
    install_private_key(os.environ["PREVIEW_SSH_PRIVATE_KEY"], ssh_dir / "id_ed25519")
    install_known_hosts(os.environ["PREVIEW_SSH_KNOWN_HOSTS"], ssh_dir / "known_hosts")


if __name__ == "__main__":
    main()
