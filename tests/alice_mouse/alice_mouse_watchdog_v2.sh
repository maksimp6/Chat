#!/system/bin/sh
# Alice Mouse boot watchdog v2, staged: no production install until approved gate.
ROOT="${ALICE_MOUSE_ROOT:-/data/adb/alice-mouse}"
BIN="$ROOT/mouse"
BACKUP="$ROOT/staging/rollback-guard"
PENDING="$ROOT/cutover.pending"
LOG="$ROOT/last.log"
TEST_MODE="${ALICE_MOUSE_TEST_MODE:-0}"

alice_mouse_active() {
    if [ "$TEST_MODE" = "1" ]; then
        [ -e "$ROOT/synthetic_active" ]
        return $?
    fi
    if [ -r /proc/bus/input/devices ] && grep -q 'Name="Alice RDC Virtual Mouse"' /proc/bus/input/devices; then
        return 0
    fi
    pidof alice_uinput_mouse >/dev/null 2>&1 && return 0
    pidof mouse >/dev/null 2>&1 && return 0
    pidof alice_mouse_daemon_locked >/dev/null 2>&1 && return 0
    return 1
}

pending_is_stale() {
    [ -f "$PENDING" ] || return 1
    boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || return 1
    transaction_boot=$(sed -n '1p' "$PENDING") || return 1
    [ "$transaction_boot" != "$boot_id" ] && return 0
    now=$(date +%s) || return 1
    last=$(stat -c %Y "$PENDING") || return 1
    age=$((now - last))
    [ "$age" -ge 45 ]
}

restore_stale_cutover() {
    [ -f "$PENDING" ] || return 0
    pending_is_stale || return 1
    alice_mouse_active && return 1
    [ -f "$BACKUP" ] || return 1
    cp "$BACKUP" "$BIN.restore.tmp" || return 1
    chmod 700 "$BIN.restore.tmp" || return 1
    mv -f "$BIN.restore.tmp" "$BIN" || return 1
    rm -f "$PENDING" || return 1
    echo "RESTORED_ORIGINAL"
    return 0
}

one_cycle() {
    if [ -f "$PENDING" ]; then
        restore_stale_cutover || return 1
    fi
    if [ "$TEST_MODE" = "1" ]; then
        return 0
    fi
    if [ -c /dev/uinput ] && ! alice_mouse_active; then
        "$BIN" serve >>"$LOG" 2>&1
    fi
    return 0
}

if [ "$TEST_MODE" = "1" ]; then
    one_cycle
else
    (while :; do
        one_cycle
        sleep 3
    done) >/dev/null 2>&1 &
fi
