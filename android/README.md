# Logos Android sources and direct SDK browser controller

## Current build

The supported direct build uses aapt2, javac, d8, zipalign and apksigner. It needs
JDK 17+, Android SDK platform 35 and build-tools 35.0.0:

```bash
bash android/scripts/test_direct.sh
ALICE_BUILD_NUMBER=123 ALICE_VERSION_NAME=0.2.0-direct bash android/scripts/build_direct.sh
```

The output is `android/build/direct/alice-pro-direct.apk`, package
`com.alicepro.mobile`. The direct manifest and Java sources are under `direct/`.
This APK is a browser controller. It does **not** embed the Flask/Python backend
or include all features of the former app. A build or successful contract check
alone does not prove operation on a phone.

## Retained embedded-backend sources

`app/src/` and `scripts/stage_python.py` remain as source material. The former
packaging configuration and dependency-updater integration have been removed
under #1423. There is no supported build of these retained sources or their old
JVM/instrumentation test suite. They must not be presented as features included
in the direct APK.

The `Release` tag workflow reports an explicit packaging blocker before using
signing credentials or publishing anything. A replacement must preserve required
features, identity and signing, pass artifact inspection, and be accepted on the
target device before release publication can resume.

The sections below describe the retained embedded-backend implementation and
historical verification; they are not a claim that the direct APK includes it.

## Diagnostics

The Android shell provides a **Diagnostics** action in the web header. It opens a native log viewer backed by app-private JSONL logs.

The logger records lifecycle, embedded-server, WebView and bridge events with levels `DEBUG`, `INFO`, `WARNING` and `ERROR`. Debug builds retain all levels; release builds retain only warnings and errors.

Diagnostic records are redacted before they reach logcat or disk. Common API keys, bearer tokens, cookies, passwords, JWTs and email addresses are removed. Logs are size-limited and rotated, and the UI supports level/time filtering, text search, event details, safe copy, export and clearing.

The exported file is generated in app cache and shared through the existing `FileProvider`. It contains the already-redacted JSONL records rather than credentials or the Yandex API key.

The retained Android test sources cover redaction and diagnostic query filtering.
Their former test packager has been removed; they are not run by the direct build.

## RootAgent (first slice, #650)

`rootagent/` is the in-app replacement for the Termux Live Server: one foreground
`RootAgentService` serving a loopback-only API on `127.0.0.1:8766`.

- Open **Diagnostics → RootAgent: панель** for owner-operated diagnostics. Opening the
  panel starts no service or request. Separate buttons start/stop the service, check
  health/root/status/device metadata, parse a requested XML hierarchy, or decode and
  preview a requested PNG. Screen requests are never included in the API/root check.
  Only one panel operation runs at a time across Activity recreation. Leaving the panel
  cancels and disconnects its HTTP client; total request deadlines also stop dripping
  responses. Results have timestamps and can be copied without
  screen content or credentials. The screenshot preview is protected from ordinary
  Android screenshots and dismissed when the activity leaves the foreground.
  Stop reports submission, not verified port closure. Unlock is explicitly unavailable:
  there is no unlock endpoint or C-driver adapter in this build. No input or grants.
  Host-side Robolectric tests exercise the actual Activity, native bitmap preview and
  lifecycle with synthetic responses; they do not prove Redmi 9 behavior. Complete
  PNG validation and post-capture Keyguard hardening remain a dependency on draft #1216;
  this panel does not accept or merge that security change.
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
  `root: true` once Magisk allows the app uid. Magisk must grant root to Logos explicitly.

## System insets

The Android WebView runs in edge-to-edge mode. Safe content padding is resolved centrally from three inset sources: system bars, display cutout and IME. The resolver takes the largest value for each edge, so a keyboard cannot reduce the navigation-bar safe area and a display cutout cannot be hidden by the system-bar inset.

Insets are requested again when the activity resumes and when the window regains focus. This covers transitions that can change system-bar or IME visibility without recreating the activity.

The resolver is unit-tested independently from Android rendering code. UI verification should cover devices with gesture and three-button navigation, display cutouts, portrait/landscape rotation and the on-screen keyboard.

## CI APK updates

The Android shell exposes an **Update APK** button in the header. In the updater you can choose a repository branch and then select one of the latest successful CI runs that produced the `alice-pro-debug-apk-<run_number>` artifact.

The updater fetches public GitHub Actions metadata, validates the artifact SHA-256 digest, verifies package name and signing certificate, rejects APKs that are not newer than the installed build, and starts the Android package installer through a `FileProvider`.

Artifact downloads use [nightly.link](https://nightly.link/) as an anonymous download proxy for GitHub Actions artifacts. GitHub's own artifact URLs are authentication-gated; nightly.link provides branch/run-specific links for public repositories. The source repository and selected workflow/run remain visible in the updater before installation.

Automatic checking of the `master` branch is also performed periodically in the background. Development/CI builds still require explicit confirmation before installation.

## Scope

This is a native shell around the existing Flask/React application, not a second Android implementation of the backend. Future mobile-specific UI work should call the same HTTP endpoints and preserve the existing server behavior.
