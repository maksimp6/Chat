#!/data/data/com.termux/files/usr/bin/bash
# Screenshots, videos and other disposable media live only in a fixed-size RAM
# disk. They never reach flash, cannot exceed the disk size, and vanish on
# unmount or reboot, so captures cannot fill the phone's storage.
#
#   redmi_media_ram.sh mount    -> prints the mounted path
#   redmi_media_ram.sh umount
set -euo pipefail

MEDIA_RAM="$HOME/.logos-media-ram"
SIZE="${LOGOS_MEDIA_RAM_SIZE:-160m}"

mounted() { grep -q "^logosmedia $MEDIA_RAM tmpfs " /proc/self/mounts; }

unmount_all() {
  local tries=0
  while mounted; do
    timeout 30 su -c "umount '$MEDIA_RAM'" || timeout 30 su -c "umount -l '$MEDIA_RAM'"
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || { echo "cannot unmount $MEDIA_RAM" >&2; exit 1; }
  done
}

case "${1:-}" in
  mount)
    test ! -L "$MEDIA_RAM"
    # A job that died before unmounting left its media in RAM; unmounting drops it.
    unmount_all
    # Anything in the folder underneath was written to flash by mistake: remove it.
    rm -rf -- "$MEDIA_RAM" 2>/dev/null || timeout 30 su -c "rm -rf -- '$MEDIA_RAM'"
    mkdir -m 700 "$MEDIA_RAM"
    context=$(ls -dZ "$HOME" | cut -d' ' -f1)
    timeout 30 su -c "mount -t tmpfs -o size=$SIZE,mode=0700,nosuid,nodev,uid=$(id -u),gid=$(id -g) logosmedia '$MEDIA_RAM' && chcon '$context' '$MEDIA_RAM'"
    mounted || { echo "RAM disk is not visible to the runner" >&2; exit 1; }
    echo "$MEDIA_RAM"
    ;;
  umount)
    unmount_all
    ;;
  *)
    echo "usage: $0 mount|umount" >&2
    exit 2
    ;;
esac
