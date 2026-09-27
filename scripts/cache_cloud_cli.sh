#!/usr/bin/env bash
set -euo pipefail

CACHE_DIR="${CLOUD_CLI_CACHE_DIR:-$HOME/.cache/alice-pro/cloud-cli}"
BASE_URL="https://sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru/cli/latest"
mkdir -p "$CACHE_DIR"

for arch in amd64 arm64; do
  file="cloud-cli-linux-${arch}.tar.gz"
  if [[ ! -s "$CACHE_DIR/$file" ]]; then
    curl -fL --retry 3 "$BASE_URL/$file" -o "$CACHE_DIR/$file"
  fi
  sha256sum "$CACHE_DIR/$file"
done
