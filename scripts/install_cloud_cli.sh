#!/usr/bin/env bash
set -euo pipefail

if command -v cloud >/dev/null 2>&1; then
    cloud --version
    exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

curl -fsSL   https://sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru/cli/latest/cloud_install.sh   -o "$tmp_dir/cloud_install.sh"

if [[ "$(id -u)" -eq 0 ]]; then
    bash "$tmp_dir/cloud_install.sh" -y
elif command -v sudo >/dev/null 2>&1; then
    sudo bash "$tmp_dir/cloud_install.sh" -y
else
    echo "Cloud.ru CLI installation requires root or sudo." >&2
    exit 1
fi

command -v cloud >/dev/null 2>&1
cloud --version
