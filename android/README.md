# Alice Pro Android wrapper

This module packages the existing Flask application in an Android shell using Chaquopy and a WebView. The backend source remains in the repository root and is included directly in the Android Python source set.

## Build

The Android module is built with Android Gradle Plugin 8.7.3 and Gradle 8.9. CI builds the debug APK on every branch/PR change.

```bash
cd android
gradle :app:assembleDebug
```

The resulting APK is generated under:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

## Runtime

- Python starts inside the Android process through Chaquopy.
- Flask listens on `127.0.0.1:5000`.
- The WebView opens `http://127.0.0.1:5000` after the server becomes reachable.
- The Yandex AI Studio API key is entered on first launch and stored in app-private preferences. It is not bundled into the APK.
- SQLite and the local repository are stored below the Python `HOME` directory supplied by Android.
- Desktop behavior remains unchanged because `ALICE_LOCAL_REPO_DIR` defaults to `/sdcard/repo` outside Android.

## Scope

This is a native shell around the existing Flask/React application, not a second Android implementation of the backend. Future mobile-specific UI work should call the same HTTP endpoints and preserve the existing server behavior.
