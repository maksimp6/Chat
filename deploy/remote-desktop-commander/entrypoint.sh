#!/usr/bin/env bash
set -euo pipefail
umask 077
config_dir="/home/node/.claude-server-commander"
auth_dir="/home/node/.desktop-commander-device"
mkdir -p "$config_dir" "$auth_dir"
chmod 700 "$config_dir" "$auth_dir"
if [[ ! -e "$config_dir/config.json" ]]; then
  cp /opt/desktop-commander/config.example.json "$config_dir/config.json"
fi
exec node /opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js "$@"
