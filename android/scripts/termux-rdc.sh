#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077

RDC_VERSION="${RDC_VERSION:-0.2.52}"
RDC_HOME="${RDC_HOME:-$HOME/.alice-rdc}"
RDC_STATE="${RDC_STATE:-$HOME/.desktop-commander-device}"
PID_FILE="$RDC_HOME/rdc.pid"
LOG_FILE="$RDC_HOME/rdc.log"
PACKAGE_DIR="$RDC_HOME/package"

mkdir -p "$RDC_HOME" "$RDC_STATE"
chmod 700 "$RDC_HOME" "$RDC_STATE"

restore_existing_state() {
  local target="$RDC_STATE/device.json"
  [[ -f "$target" ]] && return 0

  local candidates=()
  if [[ -n "${RDC_STATE_SOURCE:-}" ]]; then
    candidates+=("$RDC_STATE_SOURCE")
  fi
  candidates+=(
    "$HOME/alice-preview/services/remote-desktop-commander/state/.desktop-commander-device"
    "$HOME/.alice-rdc/state/.desktop-commander-device"
  )

  local source
  for source in "${candidates[@]}"; do
    [[ -f "$source/device.json" ]] || continue
    install -m 600 "$source/device.json" "$target"
    printf 'Reused existing RDC session from %s\n' "$source"
    return 0
  done

  return 1
}

rdc_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      printf '%s\n' "$pid"
      return 0
    fi
    rm -f "$PID_FILE"
  fi
  return 1
}

install_rdc() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf 'Installing Node.js...\n'
    pkg install -y nodejs-lts
  fi

  local entry="$PACKAGE_DIR/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"
  if [[ ! -f "$entry" ]]; then
    printf 'Installing Remote Desktop Commander %s...\n' "$RDC_VERSION"
    mkdir -p "$PACKAGE_DIR"
    if [[ ! -f "$PACKAGE_DIR/package.json" ]]; then
      printf '{"private":true}\n' >"$PACKAGE_DIR/package.json"
    fi
    npm install       --prefix "$PACKAGE_DIR"       --no-audit       --no-fund       "@wonderwhy-er/desktop-commander@$RDC_VERSION"
  fi
}

start_rdc() {
  local existing
  if existing="$(rdc_pid)"; then
    printf 'RDC already running (pid %s)\n' "$existing"
    return 0
  fi

  install_rdc
  restore_existing_state || true

  local entry="$PACKAGE_DIR/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"
  : >"$LOG_FILE"

  if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock >/dev/null 2>&1 || true
  fi

  nohup node "$entry" remote --disable-no-sleep >>"$LOG_FILE" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$PID_FILE"

  sleep 2
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    printf 'RDC failed to start. Last log lines:\n' >&2
    tail -n 30 "$LOG_FILE" >&2 || true
    exit 1
  fi

  printf 'RDC started (pid %s)\n' "$pid"
  printf 'Log: %s\n' "$LOG_FILE"
  if [[ -f "$RDC_STATE/device.json" ]]; then
    printf 'Saved RDC session found; reconnect should be automatic.\n'
  else
    printf 'No saved RDC session found on this phone. One-time pairing is required.\n'
  fi
}

stop_rdc() {
  local pid
  if ! pid="$(rdc_pid)"; then
    printf 'RDC is not running\n'
    return 0
  fi

  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"

  if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock >/dev/null 2>&1 || true
  fi

  printf 'RDC stopped\n'
}

status_rdc() {
  local pid
  if pid="$(rdc_pid)"; then
    printf 'RDC running (pid %s)\n' "$pid"
    exit 0
  fi
  printf 'RDC stopped\n'
  exit 1
}

case "${1:-start}" in
  start) start_rdc ;;
  stop) stop_rdc ;;
  restart) stop_rdc; start_rdc ;;
  status) status_rdc ;;
  logs) touch "$LOG_FILE"; tail -n "${2:-80}" "$LOG_FILE" ;;
  follow) touch "$LOG_FILE"; tail -f "$LOG_FILE" ;;
  *)
    printf 'Usage: %s {start|stop|restart|status|logs [N]|follow}\n' "$0" >&2
    exit 2
    ;;
esac
