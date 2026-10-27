#!/data/data/com.termux/files/usr/bin/bash
# Control the existing Logos installation from a native Termux shell job.
set -euo pipefail
set +x
umask 077
action=${1:-start}
trigger=${LOGOS_DEPLOY_TRIGGER:-}
if [[ -z $trigger ]]; then
  if [[ ${GITHUB_ACTIONS:-} == true ]]; then trigger=github_actions; else trigger=manual; fi
fi
case "$trigger" in manual|github_actions) ;; *) echo 'Invalid deployment trigger' >&2; exit 2 ;; esac
case "$action" in start|restart|status|stop|deploy) ;; *) echo 'Use start, restart, status, stop or deploy' >&2; exit 2 ;; esac
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
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
wifi_required=${LOGOS_REQUIRE_WIFI:-}
if [[ -z $wifi_required && -f $state/server-network.json ]]; then
  wifi_required=$("$python" -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("require_wifi", False)))' "$state/server-network.json")
fi
wifi_required=${wifi_required:-0}
[[ $wifi_required == 0 || $wifi_required == 1 ]] || exit 2
wifi_address=
if [[ $wifi_required == 1 && $action != stop ]]; then
  wifi_address=$("$python" "$script_dir/redmi_wifi_probe.py" --discover)
fi
exec 9>"$state/runner-control.lock"
flock -w 30 9 || { echo 'Another Logos control operation holds the lock' >&2; exit 1; }
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
  curl --noproxy '*' --fail --silent --max-time 3 "$base/readyz" -o "$probe/storage" || return 1
  grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$probe/storage" || return 1
  curl --noproxy '*' --fail --silent --max-time 3 "$base/" -o "$probe/index" || return 1
  grep -q '<title>Logos</title>' "$probe/index" || return 1
  local asset
  for asset in chat.js style.css; do
    curl --noproxy '*' --fail --silent --max-time 3 "$base/static/$asset" -o "$probe/$asset" || return 1
    [[ -s $probe/$asset ]] && cmp -s "$probe/$asset" "$app/static/$asset" || return 1
  done
  if [[ $wifi_required == 1 ]]; then
    "$python" "$script_dir/redmi_wifi_probe.py" --address "$wifi_address" --port "$port" \
      --assets "$app/static" >"$probe/wifi-proof.json" || return 1
  fi
  owned "$pid"
}

stop_app() {
  if [[ -n $pid ]]; then
    if alive "$pid"; then
      owned "$pid" || return 1
      kill -TERM "$pid" || return 1
      local deadline=$((SECONDS + 15))
      while alive "$pid"; do
        [[ $SECONDS -lt $deadline ]] || { echo 'Application did not stop; no replacement was started' >&2; return 1; }
        sleep 0.2
      done
    fi
    pid=
  fi
  rm -f "$state/backend.pid"
}

outcome=already_running
start_app() {
  [[ -z $pid ]] || return 0
  if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/"$1"' _ "$port" 2>/dev/null; then
    echo 'Port belongs to an untracked listener; refusing duplicate startup' >&2; return 1
  fi
  local revision launcher launcher_tmp
  revision=$(git -C "$app" rev-parse HEAD 2>/dev/null || printf unknown)
  # The app checkout changes on both deploy and rollback. A workflow-local
  # launcher stays at the event SHA and must not override the selected revision.
  launcher="$app/scripts/redmi_app_launcher.py"
  if [[ ! -f $launcher || -L $launcher || -L $app/scripts || -L $state/start-logos.py ]]; then
    echo 'The checked-out application launcher must be a regular file' >&2
    return 1
  fi
  if ! cmp -s "$launcher" "$state/start-logos.py"; then
    if [[ -f $state/start-logos.py && ! -f $state/start-logos.py.before-wifi ]]; then
      if [[ -e $state/start-logos.py.before-wifi || -L $state/start-logos.py.before-wifi ]]; then
        echo 'The saved application launcher is not a regular file' >&2
        return 1
      fi
      cp -- "$state/start-logos.py" "$state/start-logos.py.before-wifi" || return 1
    fi
    launcher_tmp=$(mktemp "$state/start-logos.py.XXXXXX") || return 1
    if ! cp -- "$launcher" "$launcher_tmp" \
        || ! chmod 700 "$launcher_tmp" \
        || ! sync -f "$launcher_tmp" \
        || ! mv -fT -- "$launcher_tmp" "$state/start-logos.py"; then
      rm -f -- "$launcher_tmp"
      return 1
    fi
  fi
  if [[ $wifi_required == 1 ]]; then
    printf '{"bind_host":"0.0.0.0","require_wifi":true}\n' >"$state/server-network.json.next" || return 1
    mv "$state/server-network.json.next" "$state/server-network.json" || return 1
  fi
  # The existing launcher supplies native dependencies and legacy ALICE_* names.
  # Bridge the same private files to LOGOS_* before entering that launcher.
  # Secrets stay out of command-line arguments and workflow environment.
  env -i HOME="$HOME" PATH="$PATH" PREFIX="${PREFIX:-}" TMPDIR="${TMPDIR:-/tmp}" \
    LD_PRELOAD="${LD_PRELOAD:-}" LANG="${LANG:-C.UTF-8}" \
    ANDROID_ROOT="${ANDROID_ROOT:-}" ANDROID_DATA="${ANDROID_DATA:-}" \
    LOGOS_APP_DIR="$app" LOGOS_STATE_DIR="$state" LOGOS_PORT="$port" \
    LOGOS_APP_REVISION="$revision" LOGOS_LOCAL_REPO_DIR="$app" LOGOS_PROJECT_ROOT="$app" \
    "$python" - "$state/start-logos.py" "$state" >"$state/runner-launch.log" 2>&1 9>&- <<'PY'
import os, pathlib, runpy, sys
state = pathlib.Path(sys.argv[2])
os.environ["LOGOS_MEMORY_PATH"] = str(state / "logos.memory")
key = state / "provider-encryption.key"
if key.is_file():
    os.environ["LOGOS_PROVIDER_CREDENTIAL_KEY"] = key.read_text().strip()
runpy.run_path(sys.argv[1], run_name="__main__")
PY
  [[ $? == 0 ]] || return 1
  [[ -f $state/backend.pid ]] || { echo 'Launcher did not write an application PID' >&2; return 1; }
  pid=$(<"$state/backend.pid")
  outcome=started
}

wait_ready() {
  [[ -n $pid ]] || { echo 'Logos is stopped' >&2; return 1; }
  local deadline=$((SECONDS + limit))
  until ready; do
    owned "$pid" || { echo 'Application exited before readiness; inspect local backend.log' >&2; return 1; }
    if [[ $SECONDS -ge $deadline ]]; then
      if [[ $outcome == started ]] && owned "$pid"; then kill -TERM "$pid"; fi
      echo 'Application readiness failed; inspect local backend.log' >&2
      [[ ! -f $probe/wifi-proof.json ]] || cat "$probe/wifi-proof.json" >&2
      return 1
    fi
    sleep 0.3
  done
}

process_revision_matches() {
  [[ -n $pid ]] && grep -zFxq "LOGOS_APP_REVISION=$1" "/proc/$pid/environ"
}

target=
receipt() {
  local status=${1:-ready} revision run wifi_check=
  [[ $pid =~ ^[0-9]+$ ]] || return 2
  revision=$(git -C "$app" rev-parse HEAD 2>/dev/null || printf unknown)
  run=${GITHUB_RUN_ID:-manual}
  [[ $run =~ ^[0-9]+$ || $run == manual ]] || return 2
  [[ $wifi_required == 0 ]] || wifi_check=',"wifi_address_http_and_assets"'
  printf '{"status":"%s","outcome":"%s","trigger":"%s","pid":%s,"url":"%s","wifi_address":"%s","app_revision":"%s","target_revision":"%s","run_id":"%s","verified_at":"%s","checks":["process_identity","healthz","html","chat_js","css"%s]}\n' \
    "$status" "$outcome" "$trigger" "$pid" "$base" "$wifi_address" "$revision" "$target" "$run" "$(date -u +%FT%TZ)" "$wifi_check" >"$state/runner-start.json.tmp" || return 1
  mv "$state/runner-start.json.tmp" "$state/runner-start.json" || return 1
  cat "$state/runner-start.json"
}

checkout_revision() {
  # Server startup needs source code, not archive videos, mirrors or 3D assets.
  # Feature-specific LFS files must be fetched explicitly when that feature needs them.
  GIT_LFS_SKIP_SMUDGE=1 git -C "$app" -c core.hooksPath=/dev/null checkout --quiet --detach "$1"
}

backup_memory() {
  [[ -f $state/logos.memory ]] || return 0
  local backup
  backup=$(mktemp "$state/deploy-backups/$previous.XXXXXX.memory") || return 1
  cp "$state/logos.memory" "$backup" && sync -f "$backup"
}

if [[ $action == deploy ]]; then
  if [[ ${GITHUB_ACTIONS:-} == true ]]; then
    [[ ${GITHUB_REPOSITORY:-} == maksimp6/Chat && ${GITHUB_REF:-} == refs/heads/develop ]] || {
      echo 'Automatic deployment accepts only this repository develop branch' >&2; exit 2;
    }
  fi
  [[ $(git -C "$app" config --get remote.origin.url) == https://github.com/maksimp6/Chat.git ]] || {
    echo 'Unexpected application repository remote' >&2; exit 1;
  }
  [[ -z $(git -C "$app" status --porcelain --untracked-files=no) ]] || {
    echo 'Tracked application changes must be committed before deployment' >&2; exit 1;
  }
  previous=$(git -C "$app" rev-parse HEAD)
  [[ $previous =~ ^[0-9a-f]{40}$ ]] || exit 2
  # Read the current branch tip even for a delayed/retried job, never downgrade
  # to an older event SHA. Archive LFS objects are not part of server deployment.
  export GIT_TERMINAL_PROMPT=0
  timeout 90 git -C "$app" fetch --quiet --no-tags origin '+refs/heads/develop:refs/remotes/origin/develop'
  target=$(git -C "$app" rev-parse --verify 'refs/remotes/origin/develop^{commit}')
  [[ $target =~ ^[0-9a-f]{40}$ ]] || exit 2
  git -C "$app" merge-base --is-ancestor "$previous" "$target" || {
    echo 'Target does not descend from the installed revision; refusing deployment' >&2; exit 1;
  }
  if [[ $previous == "$target" ]] && ready && process_revision_matches "$target"; then
    outcome=already_current
    receipt
    exit 0
  fi
  # Fail while the old backend is still serving if backup/checkout cannot fit.
  # Copying the live memory journal is unsafe: backup_memory stays after stop_app.
  "$python" - "$app" "$state" "$target" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

app, state = Path(sys.argv[1]), Path(sys.argv[2])
tree = subprocess.run(["git", "-C", str(app), "ls-tree", "-r", "-l", sys.argv[3]],
                      capture_output=True, text=True, check=True).stdout
checkout_bytes = sum(int(fields[3]) for line in tree.splitlines()
                     if len(fields := line.split(None, 4)) >= 4 and fields[3].isdigit())
memory = state / "logos.memory"
backup_bytes = memory.stat().st_size if memory.is_file() else 0
budgets = {os.stat(app).st_dev: [app, checkout_bytes]}
state_device = os.stat(state).st_dev
if state_device in budgets:
    budgets[state_device][1] += backup_bytes
else:
    budgets[state_device] = [state, backup_bytes]
for path, required in budgets.values():
    usage = os.statvfs(path)
    available = usage.f_bavail * usage.f_frsize
    print("DEPLOY_DISK_PREFLIGHT available_bytes=%d required_bytes=%d" % (available, required))
    if available <= required:
        raise SystemExit("Insufficient space for backup and checkout; backend was not stopped")
PY
  # Prepare parser dependencies while the old backend still serves requests.
  # Never compile native packages on the phone. Missing compatible wheels or a
  # download failure abort deployment before stop_app, retaining the running code.
  git -C "$app" show "$target:requirements-parsers.txt" >"$probe/requirements-parsers.txt"
  timeout 120 "$python" -m pip install --disable-pip-version-check --no-input \
    --only-binary=:all: -r "$probe/requirements-parsers.txt"
  git -C "$app" update-ref "refs/logos/deployments/$previous" "$previous"
  git -C "$app" for-each-ref --sort=-creatordate --format='%(refname)' refs/logos/deployments/ \
    | tail -n +21 | while read -r ref; do git -C "$app" update-ref -d "$ref"; done || true
  mkdir -p "$state/deploy-backups"
  stop_app
  if backup_memory && checkout_revision "$target" && start_app && wait_ready && process_revision_matches "$target"; then
    outcome=updated
    receipt
    exit 0
  fi
  echo 'Deployment failed; restoring the previous code revision' >&2
  if stop_app && checkout_revision "$previous" && start_app && wait_ready && process_revision_matches "$previous"; then
    outcome=rolled_back
    receipt rolled_back
  else
    echo 'Rollback was not ready; inspect local backend.log' >&2
  fi
  exit 1
fi

if [[ $action == restart || $action == stop ]]; then stop_app; fi
if [[ $action == stop ]]; then echo 'LOGOS_STOPPED'; exit 0; fi
if [[ $action != status ]]; then start_app; fi
wait_ready
receipt
