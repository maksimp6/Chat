#!/data/data/com.termux/files/usr/bin/bash
# Control the existing Logos installation from a native Termux shell job.
set -euo pipefail
set +x
umask 077
action=${1:-start}
case "$action" in start|restart|status|stop) ;; *) echo 'Use start, restart, status or stop' >&2; exit 2 ;; esac
app=${LOGOS_APP_DIR:-$HOME/logos-local}
state=${LOGOS_STATE_DIR:-$HOME/logos-local-state}
port=${LOGOS_PORT:-8080}
limit=${LOGOS_START_TIMEOUT:-40}
[[ $port =~ ^[0-9]+$ && $port -ge 1024 && $port -le 65535 ]] || exit 2
[[ $limit =~ ^[0-9]+$ && $limit -ge 1 && $limit -le 120 ]] || exit 2
[[ -d $app && -d $state && -f $app/app.py && -x $app/.venv/bin/python && -f $state/start-logos.py ]] || {
  echo 'The existing Logos installation is incomplete' >&2; exit 1;
}
app=$(cd "$app" && pwd -P)
state=$(cd "$state" && pwd -P)
python=$app/.venv/bin/python
exec 9>"$state/runner-control.lock"
flock -w 30 9
base=http://127.0.0.1:$port
probe=$(mktemp -d "$state/runner-probe.XXXXXX")
trap 'rm -rf "$probe"' EXIT

alive() {
  [[ $1 =~ ^[0-9]+$ && -r /proc/$1/stat ]] || return 1
  local record
  record=$(</proc/"$1"/stat)
  record=${record##*) }
  [[ ${record%% *} != Z ]] && kill -0 "$1" 2>/dev/null
}
owned() {
  alive "$1" || return 1
  [[ $(readlink -f "/proc/$1/cwd") == "$app" ]] || return 1
  local -a args=()
  mapfile -d '' -t args <"/proc/$1/cmdline"
  [[ ${#args[@]} == 2 && ${args[0]} == "$python" && ${args[1]} == app.py ]]
}
pid=
if [[ -f $state/backend.pid ]]; then
  pid=$(<"$state/backend.pid")
  [[ $pid =~ ^[0-9]+$ ]] || { echo 'Invalid application PID file' >&2; exit 1; }
  if alive "$pid"; then
    owned "$pid" || { echo 'PID file points to another process; refusing to signal it' >&2; exit 1; }
  else
    pid=
  fi
fi

ready() {
  [[ -n $pid ]] && owned "$pid" || return 1
  curl --noproxy '*' --fail --silent --max-time 3 "$base/healthz" -o "$probe/health" || return 1
  grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$probe/health" || return 1
  curl --noproxy '*' --fail --silent --max-time 3 "$base/" -o "$probe/index" || return 1
  grep -q '<title>Logos</title>' "$probe/index" || return 1
  local asset
  for asset in chat.js style.css; do
    curl --noproxy '*' --fail --silent --max-time 3 "$base/static/$asset" -o "$probe/$asset" || return 1
    [[ -s $probe/$asset ]] && cmp -s "$probe/$asset" "$app/static/$asset" || return 1
  done
  owned "$pid"
}

if [[ $action == restart || $action == stop ]]; then
  if [[ -n $pid ]]; then
    owned "$pid" || exit 1
    kill -TERM "$pid"
    deadline=$((SECONDS + 15))
    while alive "$pid"; do
      [[ $SECONDS -lt $deadline ]] || { echo 'Application did not stop; no replacement was started' >&2; exit 1; }
      sleep 0.2
    done
    rm -f "$state/backend.pid"
    pid=
  fi
  if [[ $action == stop ]]; then echo 'LOGOS_STOPPED'; exit 0; fi
fi

outcome=already_running
if [[ -z $pid && $action != status ]]; then
  if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/"$1"' _ "$port" 2>/dev/null; then
    echo 'Port belongs to an untracked listener; refusing duplicate startup' >&2; exit 1
  fi
  # Reuse the reviewed launcher and its on-phone data/dependency/key configuration.
  # The app receives no GitHub job token or inherited workflow credentials.
  env -i HOME="$HOME" PATH="$PATH" PREFIX="${PREFIX:-}" TMPDIR="${TMPDIR:-/tmp}" \
    LD_PRELOAD="${LD_PRELOAD:-}" LANG="${LANG:-C.UTF-8}" \
    ANDROID_ROOT="${ANDROID_ROOT:-}" ANDROID_DATA="${ANDROID_DATA:-}" \
    LOGOS_APP_DIR="$app" LOGOS_STATE_DIR="$state" LOGOS_PORT="$port" \
    "$python" "$state/start-logos.py" >"$state/runner-launch.log" 2>&1 9>&-
  [[ -f $state/backend.pid ]] || { echo 'Launcher did not write an application PID' >&2; exit 1; }
  pid=$(<"$state/backend.pid")
  outcome=started
fi
[[ -n $pid ]] || { echo 'Logos is stopped' >&2; exit 1; }
deadline=$((SECONDS + limit))
until ready; do
  owned "$pid" || { echo 'Application exited before readiness; inspect local backend.log' >&2; exit 1; }
  if [[ $SECONDS -ge $deadline ]]; then
    # Only terminate the new, identity-verified process from this failed attempt.
    if [[ $outcome == started ]]; then owned "$pid" && kill -TERM "$pid"; fi
    echo 'Application readiness failed; inspect local backend.log' >&2
    exit 1
  fi
  sleep 0.3
done
revision=$(git -C "$app" rev-parse HEAD 2>/dev/null || printf unknown)
run=${GITHUB_RUN_ID:-manual}
[[ $run =~ ^[0-9]+$ || $run == manual ]] || exit 2
printf '{"status":"ready","outcome":"%s","pid":%s,"url":"%s","app_revision":"%s","run_id":"%s","verified_at":"%s","checks":["process_identity","healthz","html","chat_js","css"]}\n' \
  "$outcome" "$pid" "$base" "$revision" "$run" "$(date -u +%FT%TZ)" >"$state/runner-start.json.tmp"
mv "$state/runner-start.json.tmp" "$state/runner-start.json"
cat "$state/runner-start.json"
