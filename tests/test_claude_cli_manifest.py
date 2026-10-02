"""Deterministic guard: committed Claude CLI manifest files must exist and be consistent.

These files are prerequisites for the 'Install pinned native Claude CLI' step in
.github/workflows/claude-lite.yml.  Green CI cannot omit them; missing or malformed
files fail this suite before the dialogue job can silently 404.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST_DIR = Path(__file__).parent.parent / ".github" / "claude-cli"
PACKAGE_JSON = MANIFEST_DIR / "package.json"
LOCKFILE = MANIFEST_DIR / "package-lock.json"
PINNED_VERSION = "2.1.283"
PINNED_PACKAGE = "@anthropic-ai/claude-code"
EXPECTED_INTEGRITY_PREFIX = "sha512-"
LOCKFILE_VERSION = 3


def test_package_json_exists() -> None:
    assert PACKAGE_JSON.exists(), f"Missing: {PACKAGE_JSON}"


def test_package_json_valid_json() -> None:
    data = json.loads(PACKAGE_JSON.read_text())
    assert isinstance(data, dict)


def test_package_json_pins_exact_version() -> None:
    data = json.loads(PACKAGE_JSON.read_text())
    deps = data.get("dependencies", {})
    assert PINNED_PACKAGE in deps, f"{PINNED_PACKAGE} not in dependencies"
    assert deps[PINNED_PACKAGE] == PINNED_VERSION, (
        f"Expected {PINNED_VERSION}, got {deps[PINNED_PACKAGE]}"
    )


def test_package_lock_exists() -> None:
    assert LOCKFILE.exists(), f"Missing: {LOCKFILE}"


def test_package_lock_valid_json() -> None:
    data = json.loads(LOCKFILE.read_text())
    assert isinstance(data, dict)


def test_package_lock_version() -> None:
    data = json.loads(LOCKFILE.read_text())
    assert data.get("lockfileVersion") == LOCKFILE_VERSION


def test_package_lock_contains_pinned_package() -> None:
    data = json.loads(LOCKFILE.read_text())
    packages = data.get("packages", {})
    key = f"node_modules/{PINNED_PACKAGE}"
    assert key in packages, f"Lock missing entry for {key}"
    entry = packages[key]
    assert entry["version"] == PINNED_VERSION
    assert entry.get("integrity", "").startswith(EXPECTED_INTEGRITY_PREFIX)
    assert entry.get("resolved", "").startswith("https://registry.npmjs.org/")


def test_package_lock_root_matches_package_json() -> None:
    pkg = json.loads(PACKAGE_JSON.read_text())
    lock = json.loads(LOCKFILE.read_text())
    lock_root_deps = lock.get("packages", {}).get("", {}).get("dependencies", {})
    pkg_deps = pkg.get("dependencies", {})
    for name, version in pkg_deps.items():
        assert lock_root_deps.get(name) == version, (
            f"Lock root entry for {name} ({lock_root_deps.get(name)!r}) "
            f"does not match package.json ({version!r})"
        )


def test_package_lock_linux_x64_optional_dep() -> None:
    data = json.loads(LOCKFILE.read_text())
    packages = data.get("packages", {})
    key = "node_modules/@anthropic-ai/claude-code-linux-x64"
    assert key in packages, "Lock is missing linux-x64 optional dependency"
    entry = packages[key]
    assert entry.get("optional") is True
    assert "linux" in entry.get("os", [])
    assert "x64" in entry.get("cpu", [])
    assert entry.get("integrity", "").startswith(EXPECTED_INTEGRITY_PREFIX)


def test_no_floating_version_specifier() -> None:
    pkg = json.loads(PACKAGE_JSON.read_text())
    for name, spec in pkg.get("dependencies", {}).items():
        assert not spec.startswith("^") and not spec.startswith("~"), (
            f"Dependency {name} uses floating specifier {spec!r}; pin to exact version"
        )
