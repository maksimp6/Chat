#!/usr/bin/env bash
# Two-second, native security + C keyboard fault-emulation integration gate.
# Full pytest/C watchdog/SELinux testing remains required separately.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

test -f remote_input/security/grants.py
test -f remote_input/security/socket_boundary.py
test -f remote_input/drivers/keyboard/keyboard.c
test -f remote_input/drivers/keyboard/uinput_device.c
test -f tests/remote_input_security/fast_gate.py
command -v clang >/dev/null || { echo "clang is required for native keyboard checks" >&2; exit 1; }

# Termux can unset TMPDIR and has no /tmp; use the Python toolchain prefix.
if [ -n "${TMPDIR:-}" ]; then
  tmp_root="$TMPDIR"
elif [ -d /tmp ]; then
  tmp_root=/tmp
else
  tmp_root="$(dirname "$(command -v python3)")/../tmp"
fi
scratch="$(mktemp -d "$tmp_root/alice-input-fast.XXXXXXXX")"
security_pid=""
keyboard_pid=""
cleanup() {
  if [ -n "$security_pid" ]; then kill "$security_pid" 2>/dev/null || true; fi
  if [ -n "$keyboard_pid" ]; then kill "$keyboard_pid" 2>/dev/null || true; fi
  rm -rf "$scratch"
}
trap cleanup EXIT

python3 tests/remote_input_security/fast_gate.py >"$scratch/security.log" 2>&1 &
security_pid="$!"
(
  clang -std=gnu11 -pthread -Wall -Wextra -Werror \
    remote_input/drivers/keyboard/keyboard.c \
    remote_input/drivers/keyboard/uinput_device.c \
    tests/remote_input_keyboard/test_uinput_device.c \
    -o "$scratch/keyboard"
  "$scratch/keyboard"
) >"$scratch/keyboard.log" 2>&1 &
keyboard_pid="$!"

set +e
wait "$security_pid"; security_rc=$?
wait "$keyboard_pid"; keyboard_rc=$?
set -e
security_pid=""
keyboard_pid=""

cat "$scratch/security.log"
cat "$scratch/keyboard.log"

if [ "$security_rc" -ne 0 ] || [ "$keyboard_rc" -ne 0 ]; then
  echo "ALICE_INPUT_FAST FAIL security=$security_rc keyboard=$keyboard_rc" >&2
  exit 1
fi

echo "ALICE_INPUT_FAST_INTEGRATION PASS (Security + actual C keyboard mocked uinput)"
