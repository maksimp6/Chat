#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$ROOT/android/scripts/build_direct.sh"
MANIFEST="$ROOT/android/direct/AndroidManifest.xml"
MAIN="$ROOT/android/direct/src/com/alicepro/mobile/MainActivity.java"
BROWSER="$ROOT/android/direct/src/com/alicepro/mobile/BrowserActivity.java"

grep -q 'package="com.alicepro.mobile"' "$MANIFEST"
grep -q 'aapt2' "$BUILD"
grep -q 'javac' "$BUILD"
grep -q 'd8' "$BUILD"
grep -q 'zipalign' "$BUILD"
grep -q 'apksigner' "$BUILD"
! grep -qi 'gradle' "$BUILD"
! grep -qi 'chaquopy' "$BUILD"
! grep -qi 'androidx' "$MAIN"
! grep -qi 'androidx' "$BROWSER"
grep -q '127.0.0.1' "$BROWSER"
grep -q 'CONTROL_PORT = 8765' "$BROWSER"
grep -q '"navigate"' "$BROWSER"
grep -q '"click"' "$BROWSER"
grep -q '"type"' "$BROWSER"

echo "Direct Android build contract: OK"
