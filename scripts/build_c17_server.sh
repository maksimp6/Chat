#!/usr/bin/env bash
# Builds the #1178 fallback control server to build/alice-lan-c17.
# --check also builds ASan/UBSan variants with gcc and clang and runs the
# server test suites against each before the release build.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source="$root/deploy/fallback-control/alice_lan_c17.c"
out="$root/build"
strict=(-std=c17 -Wall -Wextra -Wpedantic -Werror)
suites=(
  tests/test_alice_c17_tunnel_red.py
  tests/test_alice_c17_tunnel_extended_red.py
  tests/test_alice_c17_server_safety.py
)
mkdir -p "$out"

if [[ "${1:-}" == "--check" ]]; then
  for cc in gcc clang; do
    if ! command -v "$cc" >/dev/null 2>&1; then
      echo "Missing required compiler: $cc" >&2
      exit 1
    fi
    binary="$out/alice-lan-c17-$cc-sanitized"
    "$cc" "${strict[@]}" -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined \
      -fno-sanitize-recover=all "$source" -o "$binary"
    (cd "$root" && ALICE_C17_SERVER_BIN="$binary" python -m pytest -q -p no:cacheprovider "${suites[@]}")
    echo "$cc sanitized PASS"
  done
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

"${CC:-gcc}" "${strict[@]}" -O2 -D_FORTIFY_SOURCE=2 "$source" -o "$out/alice-lan-c17"
echo "built $out/alice-lan-c17"
