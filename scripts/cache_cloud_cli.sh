#!/usr/bin/env bash
# Refresh the local, release-maintainer cache from the official Cloud.ru source.
# Normal installations must use install_cloud_cli.sh and never call this source.
set -euo pipefail

CACHE_DIR="${CLOUD_CLI_CACHE_DIR:-$HOME/.cache/alice-pro/cloud-cli}"
OFFICIAL_BASE_URL="https://sbc-cli.obs.ru-moscow-1.hc.sbercloud.ru/cli/latest"
force=false

if [[ "${1:-}" == "--force" ]]; then
    force=true
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--force]" >&2
    exit 2
fi

mkdir -p "$CACHE_DIR"
for arch in amd64 arm64; do
    file="cloud-cli-linux-${arch}.tar.gz"
    destination="$CACHE_DIR/$file"
    if [[ "$force" == true || ! -s "$destination" ]]; then
        temporary="${destination}.tmp"
        trap 'rm -f "${temporary:-}"' EXIT
        curl --fail --location --silent --show-error --retry 3 \
            "$OFFICIAL_BASE_URL/$file" -o "$temporary"
        tar -tzf "$temporary" >/dev/null
        mv "$temporary" "$destination"
        trap - EXIT
    fi
done

(
    cd "$CACHE_DIR"
    sha256sum cloud-cli-linux-amd64.tar.gz cloud-cli-linux-arm64.tar.gz \
        > SHA256SUMS
    cat SHA256SUMS
)
