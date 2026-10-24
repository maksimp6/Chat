import hashlib
import os
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_cloud_cli.sh"


def _archive(path: Path, payload: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    cloud = path.parent / "cloud"
    cloud.write_bytes(payload)
    cloud.chmod(0o755)
    with tarfile.open(path, "w:gz") as bundle:
        bundle.add(cloud, arcname="cloud")
    return hashlib.sha256(path.read_bytes()).hexdigest()






def test_only_cache_helper_contains_official_download_host() -> None:
    official_host = "sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru"
    scripts_with_host = {
        path.name
        for path in (ROOT / "scripts").iterdir()
        if path.is_file() and official_host in path.read_text(errors="ignore")
    }
    assert scripts_with_host == {"cache_cloud_cli.sh"}


