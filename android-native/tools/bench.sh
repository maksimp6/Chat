#!/data/data/com.termux/files/usr/bin/bash
# Бенчмарк стадий: каждая стадия запускается отдельно на своих входах, время сравнивается с бюджетом.
# Использование: bench.sh   (код возврата 1, если какой-то бюджет нарушен)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
. "$HERE/perf-budget.env"
JAR="${ANDROID_JAR:-$HOME/.alice-rdc/pet-overlay/android.jar}"
KEY="$HOME/.android/debug-key.pem"
CERT="$HOME/.android/debug-cert.pem"
W="$(mktemp -d "$ROOT/build/bench.XXXX")"
trap 'rm -rf "$W"' EXIT
fail=0

timed() {  # timed <name> <budget_ms> <cmd...>: лучший из двух прогонов (первый прогон холодный)
  local name="$1" budget="$2"; shift 2
  local t0 t1 ms verdict best=999999 rc=0
  for _ in 1 2; do
    t0=$(date +%s%N); "$@" >"$W/$name.log" 2>&1; rc=$?
    t1=$(date +%s%N); ms=$(( (t1 - t0) / 1000000 ))
    [ "$ms" -lt "$best" ] && best=$ms
    [ $rc -ne 0 ] && break
  done
  ms=$best
  if [ $rc -ne 0 ]; then verdict="ERROR rc=$rc"; fail=1
  elif [ "$ms" -gt "$budget" ]; then verdict="OVER BUDGET"; fail=1
  else verdict="ok"; fi
  printf '%-10s %6s ms  budget %6s ms  %s\n' "$name" "$ms" "$budget" "$verdict"
}

timed compile "$BUDGET_COMPILE_MS" "$HERE/stages/compile.sh" "$ROOT/src/launcher.c" "$W/libalicelauncher.so"
timed link    "$BUDGET_LINK_MS"    "$HERE/stages/link.sh" "$ROOT/AndroidManifest.xml" "$JAR" "$W/base.apk"
timed sign    "$BUDGET_SIGN_MS"    "$HERE/stages/sign.sh" "$W/base.apk" "$W/libalicelauncher.so" "$W/native.apk" "$KEY" "$CERT" 1048576

exit $fail
