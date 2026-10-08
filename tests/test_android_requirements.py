from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_has_one_direct_sdk_build_path():
    android = ROOT / "android"
    build = (android / "scripts" / "build_direct.sh").read_text(encoding="utf-8")

    assert "aapt2" in build
    assert "javac" in build
    assert "d8" in build
    assert "zipalign" in build
    assert "apksigner" in build

    assert not (android / "build.gradle.kts").exists()
    assert not (android / "settings.gradle.kts").exists()
    assert not (android / "gradle.properties").exists()
    assert not (android / "app").exists()
    assert not (android / "requirements.txt").exists()


def test_android_direct_runtime_is_framework_only():
    direct = ROOT / "android" / "direct"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in direct.rglob("*")
        if path.is_file()
    ).lower()

    assert "androidx" not in text
    assert "chaquopy" not in text
    assert "kotlin" not in text
    assert "python" not in text
