#!/system/bin/sh
set -eu
TARGET='com.xiaomi.aicr/com.google.android.accessibility.selecttospeak.SelectToSpeakService'
ORIGINAL=$(settings get secure enabled_accessibility_services)
ORIGINAL_ENABLED=$(settings get secure accessibility_enabled)
case "$ORIGINAL" in
  *"$TARGET"*) ;;
  *) echo 'ERROR HyperAI not enabled'; exit 4;;
esac
OTHER=$(printf '%s' "$ORIGINAL" | sed "s#:$TARGET##;s#$TARGET:##;s#$TARGET##")
[ "$OTHER" != "$ORIGINAL" ] || exit 5
DIR=/data/local/tmp/logos-hyperai-ab
mkdir -p "$DIR"
printf '%s\n' "$ORIGINAL" > "$DIR/original-services"
printf '%s\n' "$ORIGINAL_ENABLED" > "$DIR/original-enabled"
echo "original=$ORIGINAL" > "$DIR/measurements.txt"
restore() {
 settings put secure enabled_accessibility_services "$ORIGINAL"
 settings put secure accessibility_enabled "$ORIGINAL_ENABLED"
 echo "RESTORED=$(settings get secure enabled_accessibility_services)" >> "$DIR/measurements.txt"
}
trap restore EXIT
# Independent safety watchdog, restoring original even if ADB client disconnects.
cat > "$DIR/watchdog.sh" <<EOF
#!/system/bin/sh
sleep 65
settings put secure enabled_accessibility_services '$ORIGINAL'
settings put secure accessibility_enabled '$ORIGINAL_ENABLED'
EOF
nohup sh "$DIR/watchdog.sh" >/dev/null 2>&1 </dev/null &
measure() {
 phase=$1
 p=$(pidof com.openai.chatgpt | awk '{print $1}')
 start=$(cat /proc/$p/stat | awk '{print $22}')
 a=$(awk '{print $14+$15}' /proc/$p/stat)
 uptime_a=$(cut -d' ' -f1 /proc/uptime)
 gfx_a=$(dumpsys gfxinfo com.openai.chatgpt | grep -E 'Total frames rendered:|Janky frames:' | head -2 | tr '\n' '|')
 focus_a=$(dumpsys window | grep mCurrentFocus | head -1)
 sleep 12
 p2=$(pidof com.openai.chatgpt | awk '{print $1}')
 b=$(awk '{print $14+$15}' /proc/$p/stat)
 uptime_b=$(cut -d' ' -f1 /proc/uptime)
 gfx_b=$(dumpsys gfxinfo com.openai.chatgpt | grep -E 'Total frames rendered:|Janky frames:' | head -2 | tr '\n' '|')
 focus_b=$(dumpsys window | grep mCurrentFocus | head -1)
 echo "PHASE=$phase PID=$p/$p2 START=$start TICKS=$a/$b UPTIME=$uptime_a/$uptime_b GFX=$gfx_a => $gfx_b FOCUS=$focus_a => $focus_b" | tee -a "$DIR/measurements.txt"
}
measure baseline
settings put secure enabled_accessibility_services "$OTHER"
echo "TOGGLED=$(settings get secure enabled_accessibility_services)" | tee -a "$DIR/measurements.txt"
measure hyperai_off
restore
measure restored
echo DONE
