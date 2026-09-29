from __future__ import annotations

import hashlib
import io

import pytest

from scripts import install_eds


def test_bad_checksum_preserves_existing_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(install_eds.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install_eds.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        install_eds.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"tampered")
    )
    binary = tmp_path / "eds"
    binary.write_bytes(b"existing-verified-cli")
    with pytest.raises(ValueError, match="checksum"):
        install_eds.install(tmp_path)
    assert binary.read_bytes() == b"existing-verified-cli"
    assert list(tmp_path.iterdir()) == [binary]


def test_verified_download_is_installed_atomically(tmp_path, monkeypatch):
    content = b"synthetic binary fixture, not executed"
    monkeypatch.setattr(install_eds.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install_eds.platform, "machine", lambda: "aarch64")
    monkeypatch.setitem(install_eds.CHECKSUMS, "linux-arm64", hashlib.sha256(content).hexdigest())
    urls = []

    def download(url, **kwargs):
        urls.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(install_eds.urllib.request, "urlopen", download)
    install_eds.install(tmp_path)
    assert urls == [
        "https://github.com/cloud-ru/evolution-devservices-cli/releases/download/v0.4.0/eds-linux-arm64"
    ]
    assert (tmp_path / "eds").read_bytes() == content
    assert (tmp_path / "eds").stat().st_mode & 0o777 == 0o755
