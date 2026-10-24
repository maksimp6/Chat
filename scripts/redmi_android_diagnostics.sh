#!/data/data/com.termux/files/usr/bin/bash
# Bounded Android diagnostics, executed by a GitHub Actions shell step.
set -euo pipefail
set +x
umask 077

[[ ${GITHUB_ACTIONS:-} == true ]] || { echo 'Run this through the Redmi Actions runner' >&2; exit 2; }
[[ ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]] || exit 2
[[ ${GITHUB_SHA:-} =~ ^[0-9a-f]{40}$ ]] || exit 2
base=${ANDROID_DIAGNOSTICS_HOME:-$HOME/backups/android-diagnostics}
run=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT
mkdir -p "$base/keys" "$base/runs"
chmod 700 "$base" "$base/keys" "$base/runs"
exec 9>"$base/.capture.lock"
flock -w 120 9
dest=$base/runs/$run
key=$base/keys/archive.agekey
for command in age age-keygen tar sha256sum timeout; do command -v "$command" >/dev/null; done
[[ -s $key ]] || { echo 'Archive encryption identity is missing from the phone' >&2; exit 1; }
[[ $(timeout 5 su -c 'id -u') == 0 ]] || { echo 'Android root access unavailable' >&2; exit 1; }
[[ $(timeout 5 su -c '/system/bin/getprop ro.product.model') == 'Redmi 9' ]] || exit 1
mkdir -p "$dest/raw"
if [[ -f $dest/manifest.json ]]; then
  (cd "$dest" && sha256sum -c archive.sha256)
  echo "ANDROID_ARCHIVE_REUSED run=$run"
  grep -q '"logcat_exit":0,"perfetto_exit":0,' "$dest/manifest.json"
  exit $?
fi
chmod 600 "$key"
age-keygen -y "$key" >"$base/keys/recipient.txt"
raw=$dest/raw
printf 'file\texit_code\tbytes\n' >"$raw/capture-status.tsv"
capture() {
  local name=$1 status=0
  shift
  timeout 30 "$@" >"$raw/$name" 2>&1 || status=$?
  printf '%s\t%s\t%s\n' "$name" "$status" "$(wc -c <"$raw/$name")" >>"$raw/capture-status.tsv"
  return "$status"
}

capture profile.txt su -c '
  for p in ro.product.model ro.product.manufacturer ro.product.device ro.build.version.release ro.build.version.sdk ro.product.cpu.abi; do
    printf "%s=" "$p"; /system/bin/getprop "$p"
  done
  uname -a
  cat /proc/uptime /proc/loadavg /proc/meminfo
' || true
capture battery.txt su -c '/system/bin/dumpsys battery' || true
capture cpu.txt su -c '/system/bin/dumpsys cpuinfo' || true
capture memory.txt su -c '/system/bin/dumpsys meminfo --oom' || true
log_status=0
capture logcat.txt su -c '/system/bin/logcat -b all -d -v threadtime -t 10000' || log_status=$?

# A ten-second trace; never leave a tracing daemon/loop behind.
trace=/data/misc/perfetto-traces/redmi9-$run.perfetto-trace
trace_status=0
capture perfetto-command.txt su -c "/system/bin/perfetto -o '$trace' -t 10s -b 8mb sched freq idle am wm gfx view binder_driver dalvik" || trace_status=$?
if [[ $trace_status == 0 ]]; then
  timeout 10 su -c "cat '$trace'" >"$raw/system.perfetto-trace" 2>"$raw/perfetto-read-error.txt" || trace_status=$?
  [[ -s $raw/system.perfetto-trace ]] || trace_status=1
fi
timeout 5 su -c "rm -f '$trace'" >/dev/null 2>&1 || true

tar -C "$raw" -czf "$dest/diagnostics.tar.gz" .
age -R "$base/keys/recipient.txt" -o "$dest/diagnostics.tar.gz.age.tmp" "$dest/diagnostics.tar.gz"
mv "$dest/diagnostics.tar.gz.age.tmp" "$dest/diagnostics.tar.gz.age"
# Confirm that this phone's existing key can restore every byte before publishing.
age -d -i "$key" "$dest/diagnostics.tar.gz.age" | cmp - "$dest/diagnostics.tar.gz"
(cd "$dest" && sha256sum diagnostics.tar.gz.age >archive.sha256)
digest=$(cut -d ' ' -f 1 "$dest/archive.sha256")
size=$(wc -c <"$dest/diagnostics.tar.gz.age")
printf '{"run_id":"%s","attempt":%s,"source_commit":"%s","captured_at":"%s","archive_sha256":"%s","archive_bytes":%s,"logcat_exit":%s,"perfetto_exit":%s,"trace_seconds":10,"encrypted":true}\n' \
  "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$GITHUB_SHA" "$(date -u +%FT%TZ)" "$digest" "$size" "$log_status" "$trace_status" >"$dest/manifest.json.tmp"
mv "$dest/manifest.json.tmp" "$dest/manifest.json"
sync -f "$base"
echo "ANDROID_ARCHIVE_CREATED run=$run bytes=$size sha256=$digest logcat_exit=$log_status perfetto_exit=$trace_status"
[[ $log_status == 0 && $trace_status == 0 ]]
