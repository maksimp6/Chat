# Alice Pro Android wrapper

This module packages the existing Flask application in an Android shell using Chaquopy and a WebView. The backend source remains in the repository root and is staged into the Android Python source directory before packaging.

## Build

CI stages the backend with `scripts/stage_python.py` and then builds the debug APK with Android Gradle Plugin 8.7.3 and Gradle 8.9.

```bash
cd android
python scripts/stage_python.py
gradle :app:assembleDebug
```

To produce an updateable CI APK, pass a monotonically increasing build number:

```bash
gradle -PaliceBuildNumber=123 :app:assembleDebug
```

CI uses the GitHub Actions run number as the Android `versionCode`, so newer CI runs can be installed over older CI builds.

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

## Diagnostics

The Android shell provides a **Diagnostics** action in the web header. It opens a native log viewer backed by app-private JSONL logs.

The logger records lifecycle, embedded-server, WebView and bridge events with levels `DEBUG`, `INFO`, `WARNING` and `ERROR`. Debug builds retain all levels; release builds retain only warnings and errors.

Diagnostic records are redacted before they reach logcat or disk. Common API keys, bearer tokens, cookies, passwords, JWTs and email addresses are removed. Logs are size-limited and rotated, and the UI supports level/time filtering, text search, event details, safe copy, export and clearing.

The exported file is generated in app cache and shared through the existing `FileProvider`. It contains the already-redacted JSONL records rather than credentials or the Yandex API key.

Android unit tests cover redaction and diagnostic query filtering:

```bash
cd android
gradle :app:testDebugUnitTest
```

## CI APK updates

The Android shell exposes an **Update APK** button in the header. In the updater you can choose a repository branch and then select one of the latest successful CI runs that produced the `alice-pro-debug-apk` artifact.

The updater fetches public GitHub Actions metadata, validates the artifact SHA-256 digest, verifies package name and signing certificate, rejects APKs that are not newer than the installed build, and starts the Android package installer through a `FileProvider`.

Artifact downloads use [nightly.link](https://nightly.link/) as an anonymous download proxy for GitHub Actions artifacts. GitHub's own artifact URLs are authentication-gated; nightly.link provides branch/run-specific links for public repositories. The source repository and selected workflow/run remain visible in the updater before installation.

Automatic checking of the `master` branch is also performed periodically in the background. Development/CI builds still require explicit confirmation before installation.

## Scope

This is a native shell around the existing Flask/React application, not a second Android implementation of the backend. Future mobile-specific UI work should call the same HTTP endpoints and preserve the existing server behavior.
