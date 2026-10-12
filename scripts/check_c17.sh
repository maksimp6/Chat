#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
sources=("$root/tests/c17/checked_copy.c" "$root/tests/c17/test_checked_copy.c")
for cc in gcc clang; do
  if ! command -v "$cc" >/dev/null 2>&1; then
    echo "Missing required compiler: $cc" >&2
    exit 1
  fi
  for mode in debug release; do
    flags=(-std=c17 -Wall -Wextra -Wpedantic -Werror)
    if [[ "$mode" == debug ]]; then
      flags+=(-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined)
    else
      flags+=(-O2 -DNDEBUG)
    fi
    "$cc" "${flags[@]}" "${sources[@]}" -o "$tmp/test-$cc-$mode"
    "$tmp/test-$cc-$mode"
    echo "$cc $mode PASS"
  done
done
