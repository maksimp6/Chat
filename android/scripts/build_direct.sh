#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/direct"
OUT="$ROOT/build/direct"
PACKAGE="com.alicepro.mobile"
SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
PLATFORM="${ANDROID_PLATFORM:-android-35}"
BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-35.0.0}"
VERSION_CODE="${ALICE_BUILD_NUMBER:-1}"
VERSION_NAME="${ALICE_VERSION_NAME:-0.1.0-direct}"

if [[ -z "$SDK_ROOT" ]]; then
  echo "ANDROID_SDK_ROOT or ANDROID_HOME is required" >&2
  exit 2
fi

AAPT2="$SDK_ROOT/build-tools/$BUILD_TOOLS/aapt2"
D8="$SDK_ROOT/build-tools/$BUILD_TOOLS/d8"
ZIPALIGN="$SDK_ROOT/build-tools/$BUILD_TOOLS/zipalign"
APKSIGNER="$SDK_ROOT/build-tools/$BUILD_TOOLS/apksigner"
ANDROID_JAR="$SDK_ROOT/platforms/$PLATFORM/android.jar"

for tool in "$AAPT2" "$D8" "$ZIPALIGN" "$APKSIGNER" "$ANDROID_JAR"; do
  [[ -e "$tool" ]] || { echo "Missing Android tool: $tool" >&2; exit 3; }
done

rm -rf "$OUT"
mkdir -p "$OUT/res" "$OUT/classes" "$OUT/dex"

"$AAPT2" compile --dir "$APP/res" -o "$OUT/resources.zip"

"$AAPT2" link   -o "$OUT/base-unsigned.apk"   -I "$ANDROID_JAR"   --manifest "$APP/AndroidManifest.xml"   --min-sdk-version 26   --target-sdk-version 35   --version-code "$VERSION_CODE"   --version-name "$VERSION_NAME"   --auto-add-overlay   "$OUT/resources.zip"

find "$APP/src" -name '*.java' -print0 |   xargs -0 javac     -source 17     -target 17     -encoding UTF-8     -classpath "$ANDROID_JAR"     -d "$OUT/classes"

"$D8"   --min-api 26   --lib "$ANDROID_JAR"   --output "$OUT/dex"   $(find "$OUT/classes" -name '*.class' -print)

(
  cd "$OUT/dex"
  zip -q -u "$OUT/base-unsigned.apk" classes.dex
)

"$ZIPALIGN" -f -p 4 "$OUT/base-unsigned.apk" "$OUT/alice-pro-direct-aligned.apk"

KEYSTORE="${ALICE_DEBUG_KEYSTORE_PATH:-$HOME/.android/debug.keystore}"
STOREPASS="${ALICE_DEBUG_STORE_PASSWORD:-android}"
KEYALIAS="${ALICE_DEBUG_KEY_ALIAS:-androiddebugkey}"
KEYPASS="${ALICE_DEBUG_KEY_PASSWORD:-android}"

if [[ ! -s "$KEYSTORE" ]]; then
  mkdir -p "$(dirname "$KEYSTORE")"
  keytool -genkeypair -noprompt     -keystore "$KEYSTORE"     -storepass "$STOREPASS"     -alias "$KEYALIAS"     -keypass "$KEYPASS"     -dname "CN=Android Debug,O=Alice Pro,C=US"     -keyalg RSA     -keysize 2048     -validity 10000 >/dev/null 2>&1
fi

"$APKSIGNER" sign   --ks "$KEYSTORE"   --ks-key-alias "$KEYALIAS"   --ks-pass "pass:$STOREPASS"   --key-pass "pass:$KEYPASS"   --out "$OUT/alice-pro-direct.apk"   "$OUT/alice-pro-direct-aligned.apk"

"$APKSIGNER" verify --verbose "$OUT/alice-pro-direct.apk"
sha256sum "$OUT/alice-pro-direct.apk"

echo "$OUT/alice-pro-direct.apk"
