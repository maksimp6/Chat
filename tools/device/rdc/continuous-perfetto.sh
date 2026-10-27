#!/data/data/com.termux/files/usr/bin/bash
set -u
SERIAL=192.168.31.109:42125
BASE=/data/misc/perfetto-traces
LOG="$HOME/.logos-rdc/continuous-perfetto.log"
echo "START $(date -Is) serial=$SERIAL" >> "$LOG"
while :; do
  if ! adb -s "$SERIAL" get-state 2>/dev/null | grep -qx device; then
    echo "WAIT_ADB $(date -Is)" >> "$LOG"
    sleep 10
    continue
  fi
  SLOT=$(( $(date +%s) / 60 % 6 ))
  FILE="$BASE/chatgpt-ring-$SLOT.pftrace"
  echo "CAPTURE $(date -Is) slot=$SLOT" >> "$LOG"
  adb -s "$SERIAL" shell perfetto -o "$FILE" -t 60s -b 16mb -s 32mb -a com.openai.chatgpt sched/sched_switch sched/sched_wakeup dalvik gfx view >> "$LOG" 2>&1
  rc=$?
  echo "DONE $(date -Is) slot=$SLOT status=$rc" >> "$LOG"
  sleep 1
done
