#!/data/data/com.termux/files/usr/bin/bash
# Сборка лаунчера без Gradle: aapt2 + javac + d8 + apksigner. Работает в Termux.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/build"
JAR="${ANDROID_JAR:-$HOME/.alice-rdc/pet-overlay/android.jar}"
KS="${KEYSTORE:-$HOME/.android/debug.keystore}"
XMLPULL_SRC="${XMLPULL_JAR:-/data/data/com.termux/files/usr/share/java/apktool.jar}"

rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/dex" "$OUT/gen" "$OUT/xmlpull"

# org.xmlpull нужен компилятору (в стабе android.jar его нет); в APK не попадает.
unzip -q -o "$XMLPULL_SRC" 'org/xmlpull/*' -d "$OUT/xmlpull"

aapt2 compile --dir "$HERE/res" -o "$OUT/res.zip"
aapt2 link -o "$OUT/base.apk" -I "$JAR" \
  --manifest "$HERE/AndroidManifest.xml" --java "$OUT/gen" \
  --min-sdk-version 26 --target-sdk-version 35 \
  --version-code 1 --version-name 0.1.0 "$OUT/res.zip"

find "$HERE/src" "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac -source 17 -target 17 -encoding UTF-8 -Xlint:-options \
  -classpath "$JAR:$OUT/xmlpull" -d "$OUT/classes" @"$OUT/sources.txt"
d8 --min-api 26 --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')

python3 - "$OUT" <<'PY'
import sys, zipfile
out = sys.argv[1]
src = zipfile.ZipFile(f"{out}/base.apk")
dst = zipfile.ZipFile(f"{out}/unsigned.apk", "w", zipfile.ZIP_DEFLATED)
for item in src.infolist():
    dst.writestr(item, src.read(item.filename))
dst.write(f"{out}/dex/classes.dex", "classes.dex")
dst.close()
PY

if [ ! -s "$KS" ]; then
  mkdir -p "$(dirname "$KS")"
  keytool -genkeypair -noprompt -keystore "$KS" -storepass android -keypass android \
    -alias androiddebugkey -dname "CN=Android Debug" -keyalg RSA -keysize 2048 -validity 10000 >/dev/null 2>&1
fi
apksigner sign --ks "$KS" --ks-key-alias androiddebugkey \
  --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/launcher.apk" "$OUT/unsigned.apk"
ls -l "$OUT/launcher.apk"
