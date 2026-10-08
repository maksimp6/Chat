
#!/data/data/com.termux/files/usr/bin/bash
# RDC launcher for Termux. Does not change pairing or reinstall dependencies.
set -euo pipefail
umask 077
RDC_HOME="${RDC_HOME:-$HOME/.alice-rdc}"
ENTRY="$RDC_HOME/package/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"
PID_FILE="$RDC_HOME/rdc.pid"
mkdir -p "$RDC_HOME"
status() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid cmdline
  pid="$(cat "$PID_FILE" 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)" || return 1
  [[ "$cmdline" == *desktop-commander* && "$cmdline" == *remote* ]]
}
case "${1:-start}" in
  status) status ;;
  start)
    status && { echo "RDC already running"; exit 0; }
    [[ -f "$ENTRY" ]] || { echo "RDC package not installed: $ENTRY" >&2; exit 1; }
    command -v node >/dev/null || exit 1
    nohup node "$ENTRY" remote --disable-no-sleep >>"$RDC_HOME/rdc.log" 2>&1 &
    echo "$!" >"$PID_FILE"
    sleep 2
    status || { echo "RDC did not start" >&2; exit 1; }
    echo "RDC running"
    ;;
  *) echo "Only start/status supported; never self-restart" >&2; exit 2 ;;
esac
