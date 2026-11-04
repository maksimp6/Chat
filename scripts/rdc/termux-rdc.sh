#!/data/data/com.termux/files/usr/bin/bash
# Start RDC (Desktop Commander remote agent) on the Redmi 9; Termux:Boot runs it on every boot.
printf '#!/data/data/com.termux/files/usr/bin/sh\nexec bash %s\n' "$(realpath "$0")" >"$HOME/.termux/boot/20-alice-rdc"
chmod 700 "$HOME/.termux/boot/20-alice-rdc"
su -c "echo 'Redmi 9' > /proc/sys/kernel/hostname"
pkill -f 'desktop-commander/dist/index.js remote'
node "$HOME/.alice-rdc/package/node_modules/@wonderwhy-er/desktop-commander/dist/index.js" remote --disable-no-sleep >>"$HOME/.alice-rdc/rdc.log" 2>&1 </dev/null &
