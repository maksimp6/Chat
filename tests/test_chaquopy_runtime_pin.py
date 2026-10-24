"""Regression tests for the pinned Chaquopy Python runtime fetch (Android build)."""

import hashlib
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_chaquopy_runtime as fetcher


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload: bytes):
    def opener(url, timeout):
        return _FakeResponse(payload)

    return opener


def test_pin_file_lists_the_three_runtime_files_with_sha256_digests():
    pins = fetcher.load_pins()
    assert set(pins) == {
        "target-3.14.8-0-arm64-v8a.zip",
        "target-3.14.8-0-stdlib.zip",
        "target-3.14.8-0-stdlib-pyc.zip",
    }
    assert all(len(digest) == 64 for digest in pins.values())


def test_pinned_runtime_matches_project_python_version():
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert fetcher.VERSION.startswith(python_version + ".")


def test_matching_download_is_written_and_verified(tmp_path):
    payload = b"runtime bytes"
    digest = hashlib.sha256(payload).hexdigest()
    target = fetcher.fetch("target.zip", digest, tmp_path, opener=_opener(payload))
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob("*.partial"))


def test_mismatched_download_is_rejected_and_not_kept(tmp_path):
    expected = hashlib.sha256(b"expected bytes").hexdigest()
    with pytest.raises(fetcher.RuntimeVerificationError, match="does not match pinned"):
        fetcher.fetch("target.zip", expected, tmp_path, opener=_opener(b"tampered bytes"))
    assert not (tmp_path / "target.zip").exists()
    assert not list(tmp_path.glob("*.partial"))


def test_committed_runtime_files_match_their_pins():
    runtime_dir = ROOT / "android" / "runtime"
    assert set(fetcher.verify_local(runtime_dir)) == set(fetcher.load_pins())


def test_verify_only_rejects_missing_and_tampered_files(tmp_path):
    with pytest.raises(fetcher.RuntimeVerificationError, match="missing"):
        fetcher.verify_local(tmp_path)
    name = next(iter(fetcher.load_pins()))
    (tmp_path / name).write_bytes(b"tampered bytes")
    with pytest.raises(fetcher.RuntimeVerificationError, match="does not match pinned"):
        fetcher.verify_local(tmp_path)


def test_verify_only_cli_fails_without_downloading(tmp_path, capsys):
    assert fetcher.run(["--verify-only", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().err


def test_malformed_pin_is_rejected(tmp_path):
    pin = tmp_path / "pins.sha256"
    pin.write_text("not-a-digest  target.zip\n", encoding="utf-8")
    with pytest.raises(fetcher.RuntimeVerificationError, match="malformed"):
        fetcher.load_pins(pin)


def test_empty_pin_file_is_rejected(tmp_path):
    pin = tmp_path / "pins.sha256"
    pin.write_text("# nothing pinned\n", encoding="utf-8")
    with pytest.raises(fetcher.RuntimeVerificationError, match="no pinned files"):
        fetcher.load_pins(pin)


def test_run_reports_verification_failure_with_exit_status_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetcher, "load_pins", lambda: {"target.zip": "0" * 64})
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", _opener(b"other bytes"))
    assert fetcher.run([str(tmp_path)]) == 1
    assert "does not match pinned" in capsys.readouterr().err
    assert not (tmp_path / "target.zip").exists()
