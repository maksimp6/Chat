#!/usr/bin/env bash
set -euo pipefail

RELEASE_TAG="cloud-cli-mirror-2026-09-27"
INSTALL_DIR="${CLOUD_CLI_INSTALL_DIR:-$HOME/.local/bin}"
CACHE_DIR="${CLOUD_CLI_CACHE_DIR:-$HOME/.cache/alice-pro/cloud-cli}"

case "$(uname -m)" in
  x86_64|amd64)
    ARCH=amd64
    SHA256=642d8b33afb113ffb36bd6e87fc1faf5add65fd01dae316cbd3f65c9d1c508ed
    ;;
  aarch64|arm64)
    ARCH=arm64
    SHA256=e7b977bb41616acdc474671c76835f9cb529238fe6f39c0953a53854c19b63c3
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

ASSET="cloud-cli-linux-$ARCH.tar.gz"
URL="https://github.com/maksimp6/Chat/releases/download/$RELEASE_TAG/$ASSET"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

CACHED="$CACHE_DIR/$ASSET"
if [[ -s "$CACHED" ]] && printf '%s  %s\n' "$SHA256" "$CACHED" | sha256sum --check --status -; then
  cp "$CACHED" "$TMP_DIR/$ASSET"
else
  curl --fail --location --retry 3 --output "$TMP_DIR/$ASSET" "$URL"
fi
printf '%s  %s\n' "$SHA256" "$TMP_DIR/$ASSET" | sha256sum --check -
# Keep the verified archive so later setups skip the download.
mkdir -p "$CACHE_DIR"
cp "$TMP_DIR/$ASSET" "$CACHED.tmp.$$" && mv -f "$CACHED.tmp.$$" "$CACHED"

tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR"
CLOUD_BIN="$(find "$TMP_DIR" -type f -name cloud -perm -u+x -print -quit)"
test -n "$CLOUD_BIN"

mkdir -p "$INSTALL_DIR"
install -m 755 "$CLOUD_BIN" "$INSTALL_DIR/cloud"
"$INSTALL_DIR/cloud" configure set --cli-agree-privacy-statement=true
