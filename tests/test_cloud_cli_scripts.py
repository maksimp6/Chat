import hashlib
import platform
from pathlib import Path
import shutil
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


def test_installer_uses_verified_local_cache_without_network(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }[platform.machine().lower()]
    installer_sha256 = {
        "amd64": "642d8b33afb113ffb36bd6e87fc1faf5add65fd01dae316cbd3f65c9d1c508ed",
        "arm64": "e7b977bb41616acdc474671c76835f9cb529238fe6f39c0953a53854c19b63c3",
    }[arch]
    archive = cache / f"cloud-cli-linux-{arch}.tar.gz"
    digest = _archive(archive)
    script = INSTALLER.read_text().replace(installer_sha256, digest)
    installer = tmp_path / "install.sh"
    installer.write_text(script)
    installer.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 99\n")
    (fake_bin / "curl").chmod(0o755)
    for tool in (
        "bash",
        "chmod",
        "gzip",
        "install",
        "mkdir",
        "mktemp",
        "rm",
        "sha256sum",
        "tar",
        "uname",
    ):
        resolved_tool = shutil.which(tool)
        assert resolved_tool is not None
        (fake_bin / tool).symlink_to(resolved_tool)
    install_dir = tmp_path / "installed"

    result = subprocess.run(
        [str(installer)],
        env={
            "CLOUD_CLI_CACHE_DIR": str(cache),
            "CLOUD_CLI_INSTALL_DIR": str(install_dir),
            "PATH": str(fake_bin),
            "TMPDIR": str(tmp_path),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "cloud").read_bytes() == b"#!/bin/sh\nexit 0\n"


def test_installer_download_is_pinned_to_github_release() -> None:
    script = INSTALLER.read_text()
    assert "github.com/maksimp6/Chat/releases/download/$RELEASE_TAG" in script
    assert "sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru" not in script
    assert script.count("curl ") == 1


def test_only_cache_helper_contains_official_download_host() -> None:
    official_host = "sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru"
    scripts_with_host = {
        path.name for path in (ROOT / "scripts").iterdir()
        if path.is_file() and official_host in path.read_text(errors="ignore")
    }
    assert scripts_with_host == {"cache_cloud_cli.sh"}
