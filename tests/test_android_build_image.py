"""Static contract for the Android build image (android/docker/Dockerfile).

These tests run without Docker: they check the pinning rules of the Dockerfile itself.
Building and running the image is verified by CI.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "android" / "docker" / "Dockerfile"
PIN_FILE = ROOT / "android" / "runtime" / "chaquopy-target-3.14.8-0.sha256"
ALLOWED_HOSTS = ("https://dl.google.com/", "https://repo1.maven.org/")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _args() -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S+)$", _text(), flags=re.MULTILINE))


def test_base_image_is_pinned_by_digest_not_tag_alone():
    froms = re.findall(r"^FROM (\S+)", _text(), flags=re.MULTILINE)
    assert froms, "Dockerfile has no FROM"
    for image in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"unpinned base image: {image}"


def test_no_floating_latest_tags_anywhere():
    assert ":latest" not in _text()


def test_every_download_host_is_an_allowed_source():
    for line in _text().splitlines():
        if "curl" in line:
            urls = re.findall(r"https://\S+", line)
            for url in urls:
                assert url.startswith(ALLOWED_HOSTS), f"download from unexpected host: {url}"


def test_checksum_args_are_full_sha256_digests():
    checksums = {k: v for k, v in _args().items() if k.endswith("_SHA256")}
    assert len(checksums) == 5, checksums
    for name, value in checksums.items():
        assert SHA256.match(value), f"{name} is not a SHA-256 digest"


def test_every_checksum_arg_is_verified_by_sha256sum():
    text = _text()
    for name in _args():
        if not name.endswith("_SHA256"):
            continue
        assert f"${{{name}}}" in text, f"{name} is pinned but never used"
    # Each download is followed by a sha256sum -c against the pinned value.
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("sha256sum -c") == 2 + 1, "two SDK zips plus the Chaquopy fetch helper"
    assert text.count("fetch target-3.14.8-0-") == 3


def test_chaquopy_pins_match_the_committed_pin_file():
    pins = {}
    for line in PIN_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            pins[name] = digest
    args = _args()
    assert args["CHAQUOPY_ARM64_SHA256"] == pins["target-3.14.8-0-arm64-v8a.zip"]
    assert args["CHAQUOPY_STDLIB_SHA256"] == pins["target-3.14.8-0-stdlib.zip"]
    assert args["CHAQUOPY_STDLIB_PYC_SHA256"] == pins["target-3.14.8-0-stdlib-pyc.zip"]


def test_image_carries_no_secrets():
    text = _text().upper()
    for marker in ("PASSWORD", "KEYSTORE", "TOKEN", "PRIVATE_KEY", "SECRET"):
        assert marker not in text, f"Dockerfile mentions {marker}"


def test_smoke_test_runs_every_tool_the_build_script_needs():
    text = _text()
    for command in ("aapt2 version", "d8 --version", "apksigner --version", "javac -version"):
        assert command in text, f"smoke test missing: {command}"
    assert "android.jar" in text


WORKFLOW = ROOT / ".github" / "workflows" / "android-build-image.yml"


def test_workflow_actions_are_pinned_to_full_commit_shas():
    uses = re.findall(r"^\s*uses: (\S+)", WORKFLOW.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert uses, "workflow uses no actions"
    for ref in uses:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not pinned to a commit SHA: {ref}"


def test_workflow_builds_the_image_then_the_direct_apk_inside_it():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "docker build --tag alice-android-build:ci android/docker" in text
    assert "alice-android-build:ci" in text and "build_direct.sh" in text
    assert "secrets." not in text
