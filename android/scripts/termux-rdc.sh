#!/data/data/com.termux/files/usr/bin/bash
# One-command RDC entrypoint. Never restart an active control connection.
set -euo pipefail
umask 077
RDC_HOME="${RDC_HOME:-$HOME/.alice-rdc}"
ENTRY="$RDC_HOME/package/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"
PID_FILE="$RDC_HOME/rdc.pid"
WATCHDOG_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/termux-rdc-watchdog.sh"
mkdir -p "$RDC_HOME"
healthy_pid() {
  [[ -f "$1" ]] || return 1
  local pid cmdline
  pid="$(cat "$1" 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)" || return 1
  [[ "$cmdline" == *desktop-commander* && "$cmdline" == *remote* ]]
}
start_agent() {
  exec 8>"$RDC_HOME/start.lock"
  flock -x 8
  if healthy_pid "$PID_FILE"; then
    echo "RDC process already running"
    return
  fi
  [[ -f "$ENTRY" ]] || { echo "RDC package missing: $ENTRY" >&2; return 1; }
  command -v node >/dev/null || { echo "Node.js missing" >&2; return 1; }
  nohup node "$ENTRY" remote --disable-no-sleep >>"$RDC_HOME/rdc.log" 2>&1 </dev/null &
  echo "$!" >"$PID_FILE"
  sleep 2
  healthy_pid "$PID_FILE" || { echo "RDC exited; inspect $RDC_HOME/rdc.log" >&2; return 1; }
  echo "RDC process running (remote ONLINE not yet verified)"
}
start_watchdog() {
  [[ -f "$WATCHDOG_SCRIPT" ]] || return 1
  exec 7>"$RDC_HOME/watchdog-launch.lock"
  flock -x 7
  local pid=""
  [[ -f "$RDC_HOME/watchdog.pid" ]] && pid="$(cat "$RDC_HOME/watchdog.pid" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    return
  fi
  nohup bash "$WATCHDOG_SCRIPT" >>"$RDC_HOME/watchdog.log" 2>&1 </dev/null &
  echo "$!" >"$RDC_HOME/watchdog.pid"
}
case "${1:-connect}" in
  connect|start|follow)
    command -v flock >/dev/null || { echo "flock missing" >&2; exit 1; }
    start_agent
    start_watchdog
    ;;
  status)
    healthy_pid "$PID_FILE" && echo "RDC process running; server connection unverified" || { echo "RDC process stopped"; exit 1; }
    ;;
  *) echo "Usage: $0 [status] (start/follow are compatibility aliases)" >&2; exit 2 ;;
esac
