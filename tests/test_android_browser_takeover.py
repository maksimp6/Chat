from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "android/app/src/main/java/com/alicepro/mobile/BrowserTakeoverActivity.kt"
MANIFEST = ROOT / "android/app/src/main/AndroidManifest.xml"
MAIN = ROOT / "android/app/src/main/java/com/alicepro/mobile/MainActivity.kt"


def test_android_takeover_uses_dedicated_webview_and_https_origin():
    source = ACTIVITY.read_text()
    assert 'const val TAKEOVER_HOST = "chrome-22706bfa6066.containerapps.ru"' in source
    assert 'const val TAKEOVER_URL = "https://$TAKEOVER_HOST/browser/v1/takeover"' in source
    assert "WebView(this)" in source
    assert "setAcceptThirdPartyCookies(webView, false)" in source
    assert 'target.scheme == "https" && target.host == TAKEOVER_HOST' in source
    assert "SOFT_INPUT_ADJUST_RESIZE" in source


def test_android_takeover_has_internal_bridge_and_deep_link():
    manifest = MANIFEST.read_text()
    main = MAIN.read_text()
    assert 'android:name=".BrowserTakeoverActivity"' in manifest
    assert 'android:scheme="alicepro"' in manifest
    assert 'android:host="browser"' in manifest
    assert "fun openBrowserTakeover()" in main
    assert "BrowserTakeoverActivity::class.java" in main
