#!/data/data/com.termux/files/usr/bin/bash
# Проверка неэффективного кода по находкам замеров. Падает, если найден известный лишний расход.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }

# Находка 1: каждый sha256sum — отдельный процесс (~40 мс на этом устройстве) на горячем пути.
if grep -q "sha256sum" "$HERE/tools/build-full.sh"; then
  bad "build-full.sh spawns sha256sum on the cache path (~40 ms each); use -nt"
else ok "build-full.sh cache path uses no sha256sum"; fi

# Находка 2: обёртка aarch64-linux-android-clang — bash-скрипт, он добавляет запуск shell (~25-30 мс).
if grep -v "^[[:space:]]*#" "$HERE/tools/stages/compile.sh" | grep -q "aarch64-linux-android-clang"; then
  bad "stages/compile.sh uses the bash wrapper aarch64-linux-android-clang; call clang --target directly"
else ok "stages/compile.sh calls clang directly"; fi

# Находка 3: сборка без изменений должна идти через C-точку входа без bash.
if grep -q 'execl("/data/data/com.termux/files/usr/bin/bash"' "$HERE/tools/build-native.c" && \
   grep -q "fastcheck\|up_to_date" "$HERE/tools/build-native.c"; then
  ok "build-native checks freshness in C before starting bash"
else bad "build-native.c does not check freshness before starting bash"; fi

# Находка 4: адреса данных в наших функциях должны быть одной инструкцией adr (-mcmodel=tiny),
# а не парой adrp+add. Проверяется дизассемблированием собранного .so.
SO="$HERE/build/lib/arm64-v8a/libalicelauncher.so"
if [ -f "$SO" ]; then
  n=$(llvm-objdump -d "$SO" | awk '/^[0-9a-f]+ <(on_window_created|on_destroy|ANativeActivity_onCreate)>:/{p=1;next} /^$/{p=0} p' | grep -c "adrp")
  if [ "$n" -eq 0 ]; then ok "our functions use no adrp (data addresses via adr)"
  else bad "our functions contain $n adrp (use -mcmodel=tiny so clang emits single adr)"; fi
else bad "no built .so to inspect (run make build)"; fi

exit $fail
