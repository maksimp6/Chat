# RootAgent Redmi 9 hardware acceptance (#650)

## Evidence recorded on 2026-10-13

Source: develop `97529c7571c5a642fc2fd2d8bfebe646f5c3eda6`.

Hardware observations, through the existing authorized RDC channel:

- Selected device: Redmi 9; Android reports `Redmi 9` / `galahad`.
- Existing ADB connection is authorized. All device commands selected its explicit transport; no pairing or transport changes.
- Termux `su -c 'id -u'` and explicitly quoted ADB `shell "su -c 'id -u'"` returned `0`.
- Root command lookup found `/system/bin/uiautomator`, `screencap`, `base64`, `dumpsys`.
- `dumpsys window` reported `isKeyguardShowing=true`. No screen capture or UI hierarchy was requested while locked.
- `pm list packages`, `pm path com.logospro.mobile`, and `dumpsys package com.logospro.mobile` show that Logos Pro is not installed. Thus there is no installed Logos version or APK commit to attest.
- RootAgent port 8766 has no listener. Curl status `000` is a connection failure, not an HTTP response from RootAgent.
- Existing Live Server PID 19175 stayed running. Authenticated `/health` and `/status` returned 200; anonymous `/health` returned 401. RDC was not restarted.

**BLOCKED:** RootAgentService lifecycle, app-process Magisk grant, Android Keystore, authenticated HTTP root probe, XML, PNG and locked HTTP 423 have not been tested on hardware. Root from Termux/ADB does not prove root permission for the app UID. No APK install, screen input, lock/unlock, cutover or merge was performed.

## Deterministic fault injection

`RootAgentFaultTest` executes the production loopback server and operations with injected command outcomes. It covers corrupt/truncated/CRC-damaged PNGs, lock-state transitions during capture, unknown post-capture state, authorization before execution, untrusted Keyguard output, root-command failures, output bounds and timeout-content suppression.

Baseline `b67c316d169790da9045c05f2f869dcf8a5a4000` has six RED tests exposing two implementation defects:

- PNG validation checks only eight signature bytes, accepting incomplete and corrupted images.
- Keyguard is checked only before capture; the server returns captured content if the screen locks or status becomes unknown during capture.

Do not disable these assertions or call RED merge-ready. Existing PNG success fixtures now use a complete 1x1 PNG with valid chunk CRCs instead of signature-only bytes.

The follow-up keeps all baseline assertions and fixes both defects:

- `PngScreenshotValidator` checks complete chunk structure/order/CRC, an exact final IEND, bounded dimensions and decompression, zlib integrity, scanline lengths and filter bytes. It accepts non-interlaced 8-bit RGB/RGBA, up to 8192 per dimension / 16 Mi pixels / 9 MiB encoded PNG bytes; other profiles fail closed as unavailable. This matches the expected ordinary `screencap` format, but the actual Redmi 9 output profile remains unverified until the hardware test runs. No unbounded bitmap allocation or Android-only decoder is needed in the core.
- Both successful XML and PNG captures recheck Keyguard after capture. Failed, timed-out, truncated, malformed or locked post-capture state returns 423 and discards the screen response. Error responses remain sanitized. The before/after checks narrow the capture race; they do not make an Android state query and socket delivery atomic or detect a transient lock/unlock occurring between both checks.
- Independent corrupt-zlib/scanline/chunk-order/dimension tests and a synthetic 720x1600 scanline fixture supplement the baseline faults. They do not imply an actual device screenshot was decoded.

CI publishes the app and instrumentation APKs as separate artifacts to preserve the existing app artifact layout. Retain their workflow run, actual checked-out merge SHA, full PR HEAD, file hashes, package versions and signing certificates before any installation. Hardware test root-command failures use a 5-second startup allowance; only the intentional Android sleep timeout uses 500 ms.

## Manual physical-device tests after a separately approved installation

The instrumentation runner checks the actual model/device and exact APK version name/code, refuses an occupied RootAgent port, starts the real foreground service, loads its token inside the app process, and stops only its own test service. It does not access Live Server or RDC, and never sends input. Run one instrumentation session at a time; do not run alongside another RootAgent owner. Android OS/Magisk restrictions are test failures, not grounds to bypass them.

First identify the built artifact's source SHA, versionCode, versionName, signing certificate and SHA-256. Verify installed metadata matches before running. A short SHA in versionName is not full commit provenance: retain the full artifact-build SHA separately. Set the exact version name below; do not guess it.

```sh
# The former embedded-backend packager is retired. These tests require a
# separately built and verified matching app/instrumentation pair. The direct
# browser controller is not that pair. No current build command is available.

# Existing explicit Redmi 9 ADB transport only. Run each test separately.
adb -s <existing-redmi9-transport> shell am instrument -w \
  -e expectedVersionName <exact-apk-version-name> \
  -e expectedVersionCode <exact-apk-version-code> \
  -e class com.logospro.mobile.rootagent.RootAgentHardwareTest#testAuthorizationAndReadOnlyMetadata \
  com.logospro.mobile.test/androidx.test.runner.AndroidJUnitRunner
```

Run the same command with `#testLockedScreenReturns423WithoutContent` when already locked and `#testUnlockedXmlAndDecodedPng` when already unlocked by the owner. A wrong state fails; it is not skipped and does not trigger input. Do not run the whole class as one acceptance pass: locked and unlocked tests require different physical states.

The unlocked test parses the complete XML, requires hierarchy nodes, base64-decodes and actually bitmap-decodes the PNG, then checks Keyguard again. The locked test requires exact HTTP 423 JSON without screen fields. Neither test logs XML, image bytes, tokens or private identifiers. Record test name, status, duration, installed package/version, artifact SHA and current source SHA separately. App-start failure, unreachable service, denied root and changed lock state remain failures.

CI compiles the hardware tests and runs JVM regressions; it does **not** execute hardware tests or attest Redmi 9 compatibility.

`#testRealCommandFailureOutputBoundAndTimeout` exercises a missing root utility, bounded real root output and an Android process timeout, then verifies the service still answers root probes. It creates no persistent files or input events. These are hardware fault tests only when actually executed; none ran during this audit.
