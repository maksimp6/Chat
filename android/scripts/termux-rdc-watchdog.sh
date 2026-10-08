
#!/data/data/com.termux/files/usr/bin/bash
# Independent supervisor: never stop/restart the RDC agent.
set -euo pipefail
umask 077
RDC_HOME="${RDC_HOME:-$HOME/.alice-rdc}"
RDC_SCRIPT="${RDC_SCRIPT:-$HOME/Chat/android/scripts/termux-rdc.sh}"
INTERVAL="${RDC_WATCHDOG_INTERVAL:-30}"
mkdir -p "$RDC_HOME"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] && (( INTERVAL >= 10 )) || { echo "Invalid interval" >&2; exit 2; }
command -v flock >/dev/null || exit 2
exec 9>"$RDC_HOME/watchdog.lock"
flock -n 9 || exit 0
check() {
  local pid cmdline
  [[ -f "$RDC_HOME/rdc.pid" ]] || return 1
  pid="$(cat "$RDC_HOME/rdc.pid" 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)" || return 1
  [[ "$cmdline" == *desktop-commander* && "$cmdline" == *remote* ]]
}
while :; do
  if ! check; then
    if [[ -f "$RDC_SCRIPT" ]]; then
      bash "$RDC_SCRIPT" start >>"$RDC_HOME/watchdog.log" 2>&1 || true
    else
      echo "RDC script missing" >>"$RDC_HOME/watchdog.log"
    fi
  fi
  sleep "$INTERVAL"
done
