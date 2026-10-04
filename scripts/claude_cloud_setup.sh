#!/usr/bin/env bash
# Setup script for the Claude Code cloud "ops" environment (Cloud.ru sysadmin):
# repository deps, Cloud.ru CLI, EDS CLI and GitHub CLI, reusing local caches.
# Paste into the environment's Setup script as: bash scripts/claude_cloud_setup.sh
# Credentials come from the environment's variables, never from this file.
set -euo pipefail

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
BIN="$HOME/.local/bin"
mkdir -p "$CACHE/pip" "$CACHE/npm" "$BIN"
export PATH="$BIN:$PATH" PIP_CACHE_DIR="$CACHE/pip" npm_config_cache="$CACHE/npm"

# Python and Node dependencies; package caches make repeat setups fast.
python3 -m pip install --disable-pip-version-check -q \
  -r requirements.txt -r requirements-dev.txt
npm install --ignore-scripts --no-audit --no-fund --package-lock=false --prefer-offline
npm ci --prefix deploy/chrome-worker --no-audit --no-fund --prefer-offline

# Pinned, checksum-verified Cloud.ru CLI (cached under ~/.cache/alice-pro).
bash scripts/install_cloud_cli.sh
# Pinned, checksum-verified EDS CLI.
command -v eds >/dev/null 2>&1 || python3 scripts/install_eds.py --bin-dir "$BIN"

# GitHub CLI for issues/PRs/Actions when the environment provides GH_TOKEN.
if ! command -v gh >/dev/null 2>&1; then
  GH_VERSION=2.80.0
  # SHA-256 values from the official gh_${GH_VERSION}_checksums.txt release asset.
  case "$(uname -m)" in
    x86_64|amd64) A=amd64 SUM=e0afffbf36548194aed8a1bdaf474314857a808b1b1cfa38743e2f4ed6a35b0b ;;
    aarch64|arm64) A=arm64 SUM=785a746a7b42baaf42f7d792c9ca1732451a28b78112a1a3b38884156cdeb8a2 ;;
    *) A= ;;
  esac
  if [ -n "$A" ]; then
    T="$CACHE/gh_${GH_VERSION}_linux_${A}.tar.gz"
    # A cached or fresh archive is used only after its checksum matches.
    if ! printf '%s  %s\n' "$SUM" "$T" | sha256sum --check --status 2>/dev/null; then
      rm -f "$T"
      curl -fsSL --retry 3 -o "$T" \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${A}.tar.gz"
      printf '%s  %s\n' "$SUM" "$T" | sha256sum --check --status
    fi
    tar -xzf "$T" -C "$CACHE"
    install -m 755 "$CACHE/gh_${GH_VERSION}_linux_${A}/bin/gh" "$BIN/gh"
  fi
fi

# Docker: start the daemon when present so image builds can reuse layers.
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  (dockerd >/tmp/dockerd.log 2>&1 &) || true
fi

# Report tool versions without printing any credential.
command -v cloud >/dev/null && echo "cloud: installed"
eds version 2>/dev/null | head -1 || true
gh --version 2>/dev/null | head -1 || true
docker --version 2>/dev/null || true
for name in CLOUDRU_PROJECT_ID CLOUDRU_IAM_KEY_ID CLOUDRU_IAM_KEY_SECRET EDS_API_KEY GH_TOKEN; do
  if [ -n "${!name:-}" ]; then echo "$name: set"; else echo "$name: missing"; fi
done
