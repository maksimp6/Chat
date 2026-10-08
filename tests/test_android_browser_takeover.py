from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "android/direct/src/com/alicepro/mobile/BrowserActivity.java"
MANIFEST = ROOT / "android/direct/AndroidManifest.xml"
MAIN = ROOT / "android/direct/src/com/alicepro/mobile/MainActivity.java"


def test_android_controller_uses_system_webview_and_loopback_api():
    source = BROWSER.read_text(encoding="utf-8")
    assert "new WebView(this)" in source
    assert 'CONTROL_HOST = "127.0.0.1"' in source
    assert "CONTROL_PORT = 8765" in source
    assert "new ServerSocket(" in source
    for action in ("navigate", "back", "forward", "reload", "text", "html", "click", "type", "eval"):
        assert f'"{action}"' in source


def test_android_controller_has_deep_link_and_framework_launcher():
    manifest = MANIFEST.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    assert 'android:name=".BrowserActivity"' in manifest
    assert 'android:scheme="alicepro"' in manifest
    assert 'android:host="browser"' in manifest
    assert "BrowserActivity.class" in main
    assert "androidx" not in main.lower()
