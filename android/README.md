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

## RootAgent (first slice, #650)

`rootagent/` is the in-app replacement for the Termux Live Server: one foreground
`RootAgentService` serving a loopback-only API on `127.0.0.1:8766`.

- Open **Diagnostics → RootAgent: панель** for owner-operated diagnostics. Opening the
  panel starts no service or request. Separate buttons start/stop the service, check
  health/root/status/device metadata, parse a requested XML hierarchy, or decode and
  preview a requested PNG. Screen requests are never included in the API/root check.
  Only one operation runs at a time; results have timestamps and can be copied without
  screen content or credentials. The screenshot preview is protected from ordinary
  Android screenshots and dismissed when the activity leaves the foreground.
  Stop reports submission, not verified port closure. Unlock is explicitly unavailable:
  there is no unlock endpoint or C-driver adapter in this build. No input or grants.
  The service is not exported and never starts on its own.
- Routes: `GET /v1/health`, `GET /v1/device-info` (Build model/device/SDK), `GET /v1/status`
  (`{"keyguard_locked": bool}` from a root `dumpsys window`; missing or unexpected fields count as
  locked; the foreground app is not exposed), `GET /v1/screen/tree` (`{"xml": ...}` from a root
  `uiautomator dump /proc/self/fd/1`, 15 s timeout, 2 MiB bound; failed, truncated or non-XML output is
  `502` without the raw output; the read-only counterpart of Live Server `/screen/tree`),
  `GET /v1/screen/capture` (`{"format":"png","png_base64": ...}` from a root `screencap -p | base64`,
  15 s timeout, 12 MiB bound; anything that does not decode to a PNG is `502` without the raw
  output; the read-only counterpart of Live Server `/screen/capture`),
  `GET /v1/root-probe` (`su -c "id -u"` must print `0`,
  5 s timeout). Screen tree and capture return `423 device_locked` unless `/v1/status` reads the
  lock screen as off; a timeout or unexpected output counts as locked. No shell, input, package
  or process mutation.
- Bearer token: 32 random bytes, encrypted at rest with an AndroidKeyStore AES-256-GCM key;
  it never leaves the app process. Requests with `Origin`, a cross-site `Sec-Fetch-Site` or a
  foreign `Host` are refused, so WebView pages and DNS rebinding cannot reach the API.
- JVM unit tests in `app/src/test/java/com/alicepro/mobile/rootagent/` cover the bounded shell,
  the HTTP boundary and the self-check client. Redmi 9 smoke (2026-10-13, #650): loopback-only
  listener, 401 without or with a wrong token, 403 with a foreign `Origin`, self-check
  `root: true` once Magisk allows the app uid. Magisk must grant root to Alice Pro explicitly.

## System insets

The Android WebView runs in edge-to-edge mode. Safe content padding is resolved centrally from three inset sources: system bars, display cutout and IME. The resolver takes the largest value for each edge, so a keyboard cannot reduce the navigation-bar safe area and a display cutout cannot be hidden by the system-bar inset.

Insets are requested again when the activity resumes and when the window regains focus. This covers transitions that can change system-bar or IME visibility without recreating the activity.

The resolver is unit-tested independently from Android rendering code. UI verification should cover devices with gesture and three-button navigation, display cutouts, portrait/landscape rotation and the on-screen keyboard.

## CI APK updates

The Android shell exposes an **Update APK** button in the header. In the updater you can choose a repository branch and then select one of the latest successful CI runs that produced the `alice-pro-debug-apk` artifact.

The updater fetches public GitHub Actions metadata, validates the artifact SHA-256 digest, verifies package name and signing certificate, rejects APKs that are not newer than the installed build, and starts the Android package installer through a `FileProvider`.

Artifact downloads use [nightly.link](https://nightly.link/) as an anonymous download proxy for GitHub Actions artifacts. GitHub's own artifact URLs are authentication-gated; nightly.link provides branch/run-specific links for public repositories. The source repository and selected workflow/run remain visible in the updater before installation.

Automatic checking of the `master` branch is also performed periodically in the background. Development/CI builds still require explicit confirmation before installation.

## Scope

This is a native shell around the existing Flask/React application, not a second Android implementation of the backend. Future mobile-specific UI work should call the same HTTP endpoints and preserve the existing server behavior.
