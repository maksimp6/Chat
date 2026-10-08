# Alice Pro Android wrapper

This module packages the existing Flask application in an Android shell using Chaquopy and a WebView. The backend source remains in the repository root and is staged into the Android Python source directory before packaging.

## Build

CI stages the backend with `scripts/stage_python.py` using Python 3.13 and then builds the debug APK with Gradle 9.5.0 on Java 25. `android/build.gradle.kts` declares Android Gradle Plugin 9.2.1 and Chaquopy 17.0.0; the app uses SDK 37 and JVM toolchain 17. There is no checked-in Gradle wrapper, so these commands require an installed `gradle`. See [the CI workflow](../.github/workflows/ci.yml) for the build sequence.

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

## First run

The first-launch dialog has two runtime modes:

1. **Embedded Alice Pro** — enter a Yandex AI Studio API key. The app stores it in app-private preferences, starts the embedded Python/Flask server on `127.0.0.1:5000`, bootstraps an owner identity through `POST /api/users/bootstrap`, installs the returned local identity cookie, and opens the WebView.
2. **Local agent** — choose **Local agent** and enter the HTTPS gateway URL, bootstrap token and Agent ID. The phone does not start the public Alice web server in this mode; it registers an outbound Local Tool Agent and polls the configured gateway.

The two modes are mutually selected in preferences: choosing Local agent removes the stored Yandex API key, while starting embedded Alice clears the local-agent mode flag.

The current embedded Android bootstrap passes the stored Yandex key to `android_server.py`, which exposes it to the embedded process as `YANDEX_API_KEY`. This is Android compatibility behavior and is not evidence that the general web/provider credential path or canonical Secret Store migration is complete.

No microphone permission is declared by the native Android manifest today. The web voice feature therefore must not be documented as a proven native Android microphone flow until device acceptance covers the actual WebView/media permission path.

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

## System insets

The Android WebView runs in edge-to-edge mode. Safe content padding is resolved centrally from three inset sources: system bars, display cutout and IME. The resolver takes the largest value for each edge, so a keyboard cannot reduce the navigation-bar safe area and a display cutout cannot be hidden by the system-bar inset.

Insets are requested again when the activity resumes and when the window regains focus. This covers transitions that can change system-bar or IME visibility without recreating the activity.

The resolver is unit-tested independently from Android rendering code. UI verification should cover devices with gesture and three-button navigation, display cutouts, portrait/landscape rotation and the on-screen keyboard.

## CI APK updates

The Android shell exposes an **Update APK** button in the header. In the updater you can choose a repository branch and then select one of the latest successful CI runs that produced the `alice-pro-debug-apk` artifact.

The updater fetches public GitHub Actions metadata, validates the artifact SHA-256 digest, verifies package name and signing certificate, rejects APKs that are not newer than the installed build, and starts the Android package installer through a `FileProvider`.

Artifact downloads use [nightly.link](https://nightly.link/) as an anonymous download proxy for GitHub Actions artifacts. GitHub's own artifact URLs are authentication-gated; nightly.link provides branch/run-specific links for public repositories. The source repository and selected workflow/run remain visible in the updater before installation.

Automatic checking of the `master` branch is also performed periodically in the background. Development/CI builds still require explicit confirmation before installation.

Android also requires per-app permission to install packages from this source. The manifest declares `REQUEST_INSTALL_PACKAGES`, but that declaration does not grant the user-controlled permission. If Android blocks the installer, the updater opens the system **Install unknown apps** settings for Alice Pro; enable it there, return to Alice Pro, and retry the update. APK verification (package name, signing certificate and monotonically newer versionCode) still runs before the installer is opened.

## Scope

This is a native shell around the existing Flask/React application, not a second Android implementation of the backend. Future mobile-specific UI work should call the same HTTP endpoints and preserve the existing server behavior.
