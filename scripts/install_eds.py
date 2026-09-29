#!/usr/bin/env python3
"""Install the pinned official EDS release after SHA-256 verification."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v0.4.0"
CHECKSUMS = {
    "linux-amd64": "a03f5486e2ddc44621050bae8819c237b037d9e995639ce9996b585841c29b72",
    "linux-arm64": "496e2dea8a1b24ba37bbf13f7083725e8485c11b6ba4be51d4c99ea45e77b142",
    "darwin-amd64": "0baf27d2a73f49478b1de34a82046488430c66a6243050fb8c028830ea9f45f6",
    "darwin-arm64": "cec006d077a32ea47df08cbd1736a49a961106899667a8002e2983b540b38272",
}


def install(bin_dir: Path) -> None:
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), "unsupported"
    )
    target = f"{platform.system().lower()}-{arch}"
    if target not in CHECKSUMS:
        raise ValueError("unsupported platform")
    url = (
        "https://github.com/cloud-ru/evolution-devservices-cli/releases/download/"
        f"{VERSION}/eds-{target}"
    )
    bin_dir.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=bin_dir, prefix=".eds-", delete=False) as out:
            staged = Path(out.name)
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=30) as response:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
        if digest.hexdigest() != CHECKSUMS[target]:
            raise ValueError("checksum mismatch")
        staged.chmod(0o755)
        os.replace(staged, bin_dir / "eds")
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    args = parser.parse_args()
    try:
        install(args.bin_dir.expanduser())
    except Exception:  # noqa: BLE001 - secret-safe CLI error boundary
        # Proxy/network errors can contain credentials; never echo them.
        print("EDS installation failed; check platform, network and release checksum.")
        return 1
    print(
        f"EDS {VERSION} installed; SHA-256 verified. Run eds version from the chosen bin directory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
