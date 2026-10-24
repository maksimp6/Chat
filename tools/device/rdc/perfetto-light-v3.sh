#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail
SERIAL=192.168.31.109:42125
ADB=/data/data/com.termux/files/usr/bin/adb
ROOT="$HOME/.logos-rdc/perfetto-captures"
mkdir -p "$ROOT"
RUN="$(date -u +%Y%m%dT%H%M%SZ)-$$"
DIR="$ROOT/$RUN"
mkdir -m 700 "$DIR"
printf 'run=%s mode=light duration=60\n' "$RUN" > "$DIR/manifest.txt"
if [ "${DRY_RUN:-0}" = 1 ]; then echo "DRY_RUN $DIR"; exit 0; fi
"$ADB" -s "$SERIAL" get-state | grep -qx device
for i in 1 2 3; do
  REMOTE="/data/misc/perfetto-traces/logos-light-$RUN-$i.pftrace"
  LOCAL="$DIR/segment-$i.pftrace"
  START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "START $i $START" >> "$DIR/manifest.txt"
  "$ADB" -s "$SERIAL" shell perfetto -o "$REMOTE" -t 60s -b 64mb sched/sched_switch sched/sched_wakeup > "$DIR/segment-$i.log" 2>&1
  "$ADB" -s "$SERIAL" pull "$REMOTE" "$LOCAL" > "$DIR/pull-$i.log" 2>&1
  printf 'segment=%s start=%s end=%s bytes=%s sha256=%s\n' "$i" "$START" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(stat -c %s "$LOCAL")" "$(sha256sum "$LOCAL" | cut -d ' ' -f 1)" >> "$DIR/manifest.txt"
done
echo "COMPLETE $RUN" >> "$DIR/manifest.txt"
