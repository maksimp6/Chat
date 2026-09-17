# Alice Pro Android wrapper

This module packages the existing Flask application in an Android shell using Chaquopy and a WebView.

## Important packaging rule

The Android build must receive the complete Python application source tree from the repository root. The `android_server.py` bootstrap imports the existing root `app` module; it is not a replacement backend.

Before building, copy the Python runtime sources and required non-Python assets into `android/app/src/main/python/`, preserving their relative paths. Do not modify the existing backend implementation for the Android target.

## Build

```bash
cd android
./gradlew :app:assembleDebug
```

The resulting APK is generated under:

```text
android/app/build/outputs/apk/debug/
```

## Runtime assumptions

- Flask listens on `127.0.0.1:5000`.
- The WebView opens `http://127.0.0.1:5000`.
- Python dependencies must be declared in the Chaquopy configuration and must be compatible with Android.
- Secrets must not be bundled into the APK. Configure runtime settings through the app/backend deployment mechanism.
