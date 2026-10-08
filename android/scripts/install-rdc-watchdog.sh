#!/data/data/com.termux/files/usr/bin/bash
# Opt-in installation. Preserve existing boot hooks until verified migration.
set -euo pipefail
umask 077
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR" "$HOME/.alice-rdc"
if [[ -e "$BOOT_DIR/20-alice-rdc" ]]; then
  echo "Existing boot hook detected; refusing to install a competing launcher." >&2
  exit 3
fi
if [[ -e "$BOOT_DIR/20-alice-rdc-watchdog" ]]; then
  echo "Watchdog boot hook already present; leaving unchanged."
  exit 0
fi
cat >"$BOOT_DIR/20-alice-rdc-watchdog" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec /data/data/com.termux/files/usr/bin/bash "$SOURCE_DIR/termux-rdc-watchdog.sh" >>"$HOME/.alice-rdc/watchdog-boot.log" 2>&1
EOF
chmod 700 "$BOOT_DIR/20-alice-rdc-watchdog"
echo "Boot hook installed. Requires Termux:Boot and real-device test."
