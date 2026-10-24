#!/data/data/com.termux/files/usr/bin/bash
# Тесты нативной сборки. Код возврата 0 только если все проверки прошли.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
fail=0
pass() { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }

# 1. Стандарт C11, pedantic, предупреждения как ошибки: исходник лаунчера и все host-утилиты.
if aarch64-linux-android-clang -std=c11 -pedantic -Wall -Wextra -Werror -fsyntax-only "$HERE/src/launcher.c" 2>/dev/null; then
  pass "launcher.c compiles as C11 pedantic, no warnings"
else
  bad "launcher.c violates C11 pedantic or warns"
fi
for f in build-serverd.c build-ctl.c fastcheck.c build-native.c; do
  src="$HERE/tools/$f"; [ -f "$src" ] || continue
  if clang -std=c11 -pedantic -Wall -Wextra -Werror -fsyntax-only "$src" 2>/dev/null; then
    pass "tools/$f compiles as C11 pedantic, no warnings"
  else
    bad "tools/$f violates C11 pedantic or warns"
  fi
done

# 2. Сборка: APK ровно 1 МиБ и архив целый.
bash "$HERE/tools/build-full.sh" >/dev/null 2>&1 || bad "build-full.sh failed"
APK="$HERE/build/native.apk"
size=$(stat -c %s "$APK" 2>/dev/null || echo 0)
[ "$size" -eq 1048576 ] && pass "native.apk is exactly 1048576 bytes" || bad "native.apk size is $size, expected 1048576"
unzip -tq "$APK" >/dev/null 2>&1 && pass "native.apk zip integrity (unzip -t)" || bad "native.apk zip is corrupt"
unzip -l "$APK" 2>/dev/null | grep -q "lib/arm64-v8a/libalicelauncher.so" && pass "native.apk contains arm64 .so" || bad "native.apk has no arm64 .so"
unzip -l "$APK" 2>/dev/null | grep -q "META-INF/CERT.RSA" && pass "native.apk is v1-signed (CERT.RSA present)" || bad "native.apk is not v1-signed"

# 3. Быстрая проверка актуальности: fresh -> 0, после правки исходника -> 1 (во временной копии).
mkdir -p "$HERE/build" && TMP="$(mktemp -d "$HERE/build/testcopy.XXXX")"
trap 'rm -rf "$TMP"' EXIT
cp -r "$HERE/src" "$HERE/AndroidManifest.xml" "$HERE/tools" "$TMP/" 2>/dev/null
mkdir -p "$TMP/build" && cp "$APK" "$TMP/build/native.apk"
touch -d '2000-01-01' "$TMP/src/launcher.c" "$TMP/AndroidManifest.xml" "$TMP/tools/build-full.sh" "$TMP/tools/sign_v1.py"
if "$HERE/tools/fastcheck" "$TMP"; then pass "fastcheck: fresh APK reported up to date"; else bad "fastcheck: fresh APK reported stale"; fi
touch "$TMP/src/launcher.c"
if "$HERE/tools/fastcheck" "$TMP"; then bad "fastcheck: changed source not detected"; else pass "fastcheck: changed source detected"; fi

echo
[ $fail -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $fail
