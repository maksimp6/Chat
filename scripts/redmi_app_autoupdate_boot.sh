#!/data/data/com.termux/files/usr/bin/bash
# One persistent supervisor for the existing installation; no RDC lifecycle operations.
set -u
umask 077
state=${LOGOS_STATE_DIR:-$HOME/logos-local-state}
app=${LOGOS_APP_DIR:-$HOME/logos-local}
interval=${LOGOS_AUTOUPDATE_INTERVAL:-30}
[[ $interval =~ ^[0-9]+$ && $interval -ge 1 && $interval -le 3600 ]] || exit 2
[[ -d $state && -d $app && -f $state/app-autoupdate.py && -x $app/.venv/bin/python ]] || exit 1
mode=${1:-start}
case "$mode" in
  start)
    script=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")
    nohup "$BASH" "$script" run </dev/null >>"$state/autoupdate-supervisor.log" 2>&1 &
    exit 0
    ;;
  run) ;;
  *) echo 'Use start or run' >&2; exit 2 ;;
esac
exec 9>>"$state/autoupdate-supervisor.lock"
flock -n 9 || exit 0
: >"$state/autoupdate-supervisor.lock"
printf '%s\n' "$$" >&9
trap 'exit 0' TERM INT
while :; do
  # Reload the state copy every time; a successful worker refreshes itself atomically.
  "$app/.venv/bin/python" "$state/app-autoupdate.py" --once 9>&-
  # Failure/crash is retried without misreporting a successful deployment.
  sleep "$interval" &
  wait "$!" || true
done
