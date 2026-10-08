
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR" "$HOME/.alice-rdc"
cat >"$BOOT_DIR/20-alice-rdc-watchdog" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec /data/data/com.termux/files/usr/bin/bash "$SOURCE_DIR/termux-rdc-watchdog.sh" >>"$HOME/.alice-rdc/watchdog-boot.log" 2>&1
EOF
chmod 700 "$BOOT_DIR/20-alice-rdc-watchdog"
echo "Boot hook installed. Requires Termux:Boot and a real reboot test."
