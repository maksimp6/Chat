"""Portable regression tests for Live Server token creation (no Android device)."""
import ast
import os
from pathlib import Path
import stat
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1] / "android/live_server/android_live_server.py"


def _token_function():
    tree = ast.parse(SOURCE.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "ensure_token")
    return ast.Module(body=[fn], type_ignores=[])


def test_token_created_private_even_with_public_umask(tmp_path):
    import secrets
    ns = {"os": os, "secrets": secrets, "BASE": tmp_path, "TOKEN_PATH": tmp_path / "live-token"}
    exec(compile(_token_function(), str(SOURCE), "exec"), ns)
    previous = os.umask(0)
    try:
        token = ns["ensure_token"]()
    finally:
        os.umask(previous)
    assert len(token) >= 32
    assert stat.S_IMODE(ns["TOKEN_PATH"].stat().st_mode) == 0o600
    assert ns["ensure_token"]() == token


def test_token_symlink_rejected(tmp_path):
    import secrets
    victim = tmp_path / "victim"
    victim.write_text("original")
    link = tmp_path / "live-token"
    link.symlink_to(victim)
    ns = {"os": os, "secrets": secrets, "BASE": tmp_path, "TOKEN_PATH": link}
    exec(compile(_token_function(), str(SOURCE), "exec"), ns)
    import pytest
    with pytest.raises(OSError):
        ns["ensure_token"]()
    assert victim.read_text() == "original"


def test_insecure_existing_token_rejected(tmp_path):
    import secrets
    path = tmp_path / "live-token"
    path.write_text("old")
    path.chmod(0o644)
    ns = {"os": os, "secrets": secrets, "BASE": tmp_path, "TOKEN_PATH": path}
    exec(compile(_token_function(), str(SOURCE), "exec"), ns)
    import pytest
    with pytest.raises(PermissionError):
        ns["ensure_token"]()
