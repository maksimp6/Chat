#!/data/data/com.termux/files/usr/bin/bash
# Оркестратор: вызывает стадии tools/stages/* и решает, что нужно пересобрать.
# Стадии ничего не знают друг о друге: каждая получает вход и выход аргументами.
# Кэш: .so — по хэшу исходника, base.apk — по хэшу манифеста, APK — по хэшу итоговых байт.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/build"
JAR="${ANDROID_JAR:-$HOME/.alice-rdc/pet-overlay/android.jar}"
KEY="${SIGN_KEY:-$HOME/.android/debug-key.pem}"
CERT="${SIGN_CERT:-$HOME/.android/debug-cert.pem}"
SO="$OUT/lib/arm64-v8a/libalicelauncher.so"
TIMINGS="$OUT/timings.txt"
STAGES="$HERE/tools/stages"

mkdir -p "$OUT/lib/arm64-v8a"
: > "$TIMINGS"

step() {  # step <name> <cmd...>: время шага пишется в timings.txt
  local name="$1"; shift
  local t0 t1
  t0=$(date +%s%N)
  "$@"
  t1=$(date +%s%N)
  echo "$name $(( (t1 - t0) / 1000000 ))" >> "$TIMINGS"
}

# Решения через встроенные проверки bash (-nt): без порождения процессов, ~0 мс.
# Пересборка при изменении входа чуть консервативнее хэшей (touch тоже триггерит), это безопасно.
if [ ! -f "$SO" ] || [ "$HERE/src/launcher.c" -nt "$SO" ]; then
  step compile "$STAGES/compile.sh" "$HERE/src/launcher.c" "$SO"
fi

if [ ! -f "$OUT/base.apk" ] || [ "$HERE/AndroidManifest.xml" -nt "$OUT/base.apk" ]; then
  step link "$STAGES/link.sh" "$HERE/AndroidManifest.xml" "$JAR" "$OUT/base.apk"
fi

if [ -f "$OUT/native.apk" ] && [ ! "$SO" -nt "$OUT/native.apk" ] && [ ! "$OUT/base.apk" -nt "$OUT/native.apk" ]; then
  echo "up to date: native.apk reused"
  exit 0
fi

step sign "$STAGES/sign.sh" "$OUT/base.apk" "$SO" "$OUT/native.apk" "$KEY" "$CERT" 1048576
ls -l "$OUT/native.apk"
