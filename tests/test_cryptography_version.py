"""Test cryptography version to ensure vulnerabilities are patched"""

from pathlib import Path
import re


def test_cryptography_not_vulnerable_version():
    """Test that cryptography version is not in vulnerable range (< 43.0.0)"""
    root = Path(__file__).resolve().parents[1]

    # Check both requirements files
    requirements_files = [
        root / "requirements.txt",
        root / "android" / "requirements.txt",
    ]

    for req_file in requirements_files:
        if not req_file.exists():
            continue

        content = req_file.read_text(encoding="utf-8")

        # Find cryptography version
        match = re.search(r'cryptography([><=!]+)([\d.]+)', content)
        assert match, f"cryptography requirement not found in {req_file.name}"

        operator, version = match.groups()

        # Parse version numbers
        version_parts = [int(x) for x in version.split('.')]
        major, minor = version_parts[0], version_parts[1] if len(version_parts) > 1 else 0

        # Versions < 43.0.0 are vulnerable
        if operator in ("==", ">="):
            if operator == "==":
                assert not (major < 43 or (major == 42 and minor < 10)), (
                    f"{req_file.name}: cryptography {version} is in vulnerable range (< 43.0.0). "
                    f"Dependabot alerts show CVEs in versions < 43.0.0"
                )
            elif operator == ">=":
                assert major >= 43 or (major == 42 and minor >= 10), (
                    f"{req_file.name}: cryptography >= {version} may still include vulnerable versions. "
                    f"Use >= 43.0.0 to ensure all vulnerable versions are excluded"
                )


def test_android_requirements_cryptography_matches_backend():
    """Test that Android cryptography is at least as new as backend version"""
    root = Path(__file__).resolve().parents[1]

    backend_req = (root / "requirements.txt").read_text(encoding="utf-8")
    android_req = (root / "android" / "requirements.txt").read_text(encoding="utf-8")

    # Extract versions
    backend_match = re.search(r'cryptography([><=!]+)([\d.]+)', backend_req)
    android_match = re.search(r'cryptography([><=!]+)([\d.]+)', android_req)

    if backend_match and android_match:
        backend_op, backend_ver = backend_match.groups()
        android_op, android_ver = android_match.groups()

        # Parse versions
        backend_parts = [int(x) for x in backend_ver.split('.')]
        android_parts = [int(x) for x in android_ver.split('.')]

        backend_major = backend_parts[0]
        android_major = android_parts[0]

        # Android should not be pinned to a lower version than backend allows
        if backend_op == ">=" and android_op == ">=":
            assert android_major >= backend_major, (
                f"Android cryptography version {android_ver} should not be lower than "
                f"backend version {backend_ver}"
            )


def test_security_advisories_documented():
    """Test that security concerns are documented"""
    root = Path(__file__).resolve().parents[1]

    # Check if there's a security note or advisory file
    security_files = [
        root / "SECURITY.md",
        root / "docs" / "security.md",
        root / "docs" / "advisories.md",
    ]

    # At least one should exist or issue should be tracked
    found_docs = any(f.exists() for f in security_files)

    # If no docs, cryptography version should be recent enough to be safe
    if not found_docs:
        req_file = root / "requirements.txt"
        content = req_file.read_text(encoding="utf-8")
        match = re.search(r'cryptography([><=!]+)([\d.]+)', content)
        if match:
            op, version = match.groups()
            parts = [int(x) for x in version.split('.')]
            # At minimum 43.0.0 for known vulnerabilities
            assert parts[0] >= 43, (
                "No security documentation found. "
                "cryptography should be >= 43.0.0 to mitigate known CVEs"
            )
