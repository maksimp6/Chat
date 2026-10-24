#!/data/data/com.termux/files/usr/bin/bash
# Independent Termux supervisor. No stop/restart of live RDC.
set -euo pipefail
umask 077
RDC_HOME="${RDC_HOME:-$HOME/.alice-rdc}"
RDC_SCRIPT="${RDC_SCRIPT:-$HOME/Chat/android/scripts/termux-rdc.sh}"
INTERVAL="${RDC_WATCHDOG_INTERVAL:-30}"
BACKOFF="${RDC_WATCHDOG_BACKOFF:-120}"
mkdir -p "$RDC_HOME"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] && (( INTERVAL >= 10 )) || exit 2
[[ "$BACKOFF" =~ ^[0-9]+$ ]] && (( BACKOFF >= 30 )) || exit 2
command -v flock >/dev/null || exit 2
exec 9>"$RDC_HOME/watchdog.lock"
flock -n 9 || exit 0
healthy() {
  local pid cmdline
  [[ -f "$RDC_HOME/rdc.pid" ]] || return 1
  pid="$(cat "$RDC_HOME/rdc.pid" 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)" || return 1
  [[ "$cmdline" == *desktop-commander* && "$cmdline" == *remote* ]]
}
last_attempt=0
while :; do
  if ! healthy; then
    now="$(date +%s)"
    if (( now - last_attempt >= BACKOFF )); then
      last_attempt="$now"
      if [[ -f "$RDC_SCRIPT" ]]; then
        bash "$RDC_SCRIPT" start >>"$RDC_HOME/watchdog.log" 2>&1 || true
      fi
    fi
  fi
  sleep "$INTERVAL"
done
