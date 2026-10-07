from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_build_has_no_embedded_python_runtime():
    app_gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    root_gradle = (ROOT / "android" / "build.gradle.kts").read_text(encoding="utf-8")

    assert "com.chaquo.python" not in app_gradle
    assert "com.chaquo.python" not in root_gradle
    assert "chaquopy" not in app_gradle.lower()
    assert not (ROOT / "android" / "requirements.txt").exists()
    assert not (ROOT / "android" / "scripts" / "stage_python.py").exists()
    assert not (ROOT / "android" / "app" / "src" / "main" / "python" / "android_server.py").exists()


def test_android_main_activity_is_native_controller():
    source = (
        ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "alicepro" / "mobile"
        / "MainActivity.kt"
    ).read_text(encoding="utf-8")

    assert "com.chaquo.python" not in source
    assert "Python.start" not in source
    assert "startPythonServer" not in source
    assert "BrowserTakeoverActivity::class.java" in source
