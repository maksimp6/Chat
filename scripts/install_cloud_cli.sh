#!/usr/bin/env bash
set -euo pipefail

# Archives are maintained by scripts/cache_cloud_cli.sh and mirrored in this
# repository so an installation never needs to contact Cloud.ru.
readonly RELEASE_TAG="cloud-cli-mirror-2026-09-27"
readonly RELEASE_BASE_URL="https://github.com/maksimp6/Chat/releases/download/$RELEASE_TAG"
readonly CACHE_DIR="${CLOUD_CLI_CACHE_DIR:-$HOME/.cache/alice-pro/cloud-cli}"
readonly INSTALL_DIR="${CLOUD_CLI_INSTALL_DIR:-$HOME/.local/bin}"

if command -v cloud >/dev/null 2>&1; then
    echo "Cloud.ru CLI is already installed at $(command -v cloud)."
    exit 0
fi

case "$(uname -m)" in
    x86_64|amd64)
        arch=amd64
        expected_sha256=642d8b33afb113ffb36bd6e87fc1faf5add65fd01dae316cbd3f65c9d1c508ed
        ;;
    aarch64|arm64)
        arch=arm64
        expected_sha256=e7b977bb41616acdc474671c76835f9cb529238fe6f39c0953a53854c19b63c3
        ;;
    *)
        echo "Unsupported Cloud.ru CLI architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

archive_name="cloud-cli-linux-${arch}.tar.gz"
cached_archive="$CACHE_DIR/$archive_name"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

if [[ -s "$cached_archive" ]] && \
        echo "$expected_sha256  $cached_archive" | sha256sum --check --status; then
    archive="$cached_archive"
else
    archive="$tmp_dir/$archive_name"
    curl --fail --location --silent --show-error --retry 3 \
        "$RELEASE_BASE_URL/$archive_name" -o "$archive"
    if ! echo "$expected_sha256  $archive" | sha256sum --check --status; then
        echo "Cloud.ru CLI archive checksum verification failed." >&2
        exit 1
    fi
fi

tar -xzf "$archive" -C "$tmp_dir" cloud
chmod 0755 "$tmp_dir/cloud"

if mkdir -p "$INSTALL_DIR" 2>/dev/null && [[ -w "$INSTALL_DIR" ]]; then
    install -m 0755 "$tmp_dir/cloud" "$INSTALL_DIR/cloud"
elif command -v sudo >/dev/null 2>&1; then
    sudo install -d "$INSTALL_DIR"
    sudo install -m 0755 "$tmp_dir/cloud" "$INSTALL_DIR/cloud"
else
    echo "Cloud.ru CLI installation requires write access to $INSTALL_DIR or sudo." >&2
    exit 1
fi

echo "Installed Cloud.ru CLI to $INSTALL_DIR/cloud."
