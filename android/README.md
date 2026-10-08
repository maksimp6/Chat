# Alice Pro Android

Alice Pro Android is a minimal framework-only browser controller built directly with the Android SDK tools.

## Architecture

The Android app intentionally avoids Gradle, Kotlin, AndroidX, embedded Python, Chaquopy and bundled browser engines.

Runtime:
- Java + Android framework APIs
- System WebView
- loopback browser-control API on `127.0.0.1:8765`

Build pipeline:
1. `aapt2 compile/link`
2. `javac`
3. `d8`
4. `zipalign`
5. `apksigner`

The single build entrypoint is:

```bash
bash android/scripts/build_direct.sh
```

The signed APK is written to:

```text
android/build/direct/alice-pro-direct.apk
```

## CI budgets

The Android lane enforces:
- direct APK build: **<= 15 seconds**
- signed APK size: **<= 1 MiB (1,048,576 bytes)**

A regression above either budget fails CI.

Android-only changes use the isolated `.github/workflows/android-direct.yml` lane and must not trigger unrelated backend, PostgreSQL, MCP or infrastructure suites.

## Browser control

Open the controlled browser from the app, then use the loopback API:

```bash
curl http://127.0.0.1:8765/health
```

Navigate:

```bash
curl -X POST http://127.0.0.1:8765/command \
  -H 'Content-Type: application/json' \
  -d '{"action":"navigate","url":"https://example.com"}'
```

Read text:

```bash
curl -X POST http://127.0.0.1:8765/command \
  -H 'Content-Type: application/json' \
  -d '{"action":"text"}'
```

Supported commands are `navigate`, `back`, `forward`, `reload`, `text`, `html`, `click`, `type` and `eval`.

The control server binds only to loopback and exists only while the browser activity is alive.

## Python functions

Python runtime / local Cloud-Functions-like execution is a separate optional capability tracked in #1032. It must not be part of the normal APK build critical path or force browser-only changes to rebuild Python artifacts.
