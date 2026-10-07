from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "android/app/src/main/java/com/alicepro/mobile/BrowserTakeoverActivity.kt"
MANIFEST = ROOT / "android/app/src/main/AndroidManifest.xml"
MAIN = ROOT / "android/app/src/main/java/com/alicepro/mobile/MainActivity.kt"


def test_android_controller_uses_local_webview_and_loopback_api():
    source = ACTIVITY.read_text(encoding="utf-8")
    assert "WebView(this)" in source
    assert 'const val CONTROL_HOST = "127.0.0.1"' in source
    assert "const val CONTROL_PORT = 8765" in source
    assert "ServerSocket(" in source
    assert '"navigate"' in source
    assert '"click"' in source
    assert '"type"' in source
    assert '"text"' in source
    assert "SOFT_INPUT_ADJUST_RESIZE" in source


def test_android_controller_has_deep_link_and_native_launcher_entry():
    manifest = MANIFEST.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    assert 'android:name=".BrowserTakeoverActivity"' in manifest
    assert 'android:scheme="alicepro"' in manifest
    assert 'android:host="browser"' in manifest
    assert "BrowserTakeoverActivity::class.java" in main
    assert "Open controlled browser" in main
