#!/data/data/com.termux/files/usr/bin/bash
# Проверка оптимизации adr: small.bin собран из hello.bin заменой movz+movk на adr.
# Проверяются: точный размер, наличие adr по смещению 0x78+4, отсутствие movk, одинаковый вывод и rc.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }

cp "$HERE/hello.bin" "$T/hello" && cp "$HERE/small.bin" "$T/small" && chmod +x "$T/hello" "$T/small"

[ "$(stat -c %s "$HERE/hello.bin")" -eq 176 ] && ok "hello.bin is 176 bytes" || bad "hello.bin size is not 176"
[ "$(stat -c %s "$HERE/small.bin")" -eq 172 ] && ok "small.bin is 172 bytes" || bad "small.bin size is not 172"

# Инструкция adr x1 по смещению 0x7c (entry 0x78 + 4): байты e1 00 00 10 (adr x1, +28)
adr_bytes=$(od -An -tx1 -j $((0x7c)) -N 4 "$HERE/small.bin" | tr -d ' \n')
[ "$adr_bytes" = "e1000010" ] && ok "small.bin has adr x1 at 0x7c" || bad "adr x1 not found at 0x7c (got $adr_bytes)"

# В hello.bin на том же месте должен быть movz x1 (d2801381), а не adr
movz_bytes=$(od -An -tx1 -j $((0x7c)) -N 4 "$HERE/hello.bin" | tr -d ' \n')
[ "$movz_bytes" = "811380d2" ] && ok "hello.bin has movz x1 at 0x7c (no adr)" || bad "hello.bin movz x1 not found (got $movz_bytes)"

o1=$("$T/hello"; echo "rc=$?"); o2=$("$T/small"; echo "rc=$?")
[ "$o1" = "$o2" ] && ok "same stdout and exit code: $(echo "$o1" | tr '\n' ' ')" || bad "output differs: [$o1] vs [$o2]"

exit $fail
