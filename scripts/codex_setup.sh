#!/usr/bin/env bash
set -euxo pipefail
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# Setup runs from the checkout Codex already prepared. Network access and a
# configured Git remote are not prerequisites: install the scripts we have.
for name in codex_setup.sh codex_maintenance.sh codex_cloudru_env.sh codex_agent_credentials.sh; do
  test -f "scripts/$name"
  install -m 700 "scripts/$name" "$INSTALL_DIR/$name"
done

# Cloud.ru secrets are available during setup only. Normalize the legacy
# Codex names and materialize a mode-600 hand-off for the deploy CLI.
# shellcheck disable=SC1091
source scripts/codex_cloudru_env.sh

python --version | grep -q '3.14'
node --version | grep -q '^v22\.'
java -version 2>&1 | head -1 | grep -q '"25'

python -m pip install --disable-pip-version-check \
  -r requirements.txt -r requirements-dev.txt
npm install --ignore-scripts --no-audit --no-fund --package-lock=false

git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.program gpg

export PATH="$BIN_DIR:$PATH"

# GitHub CLI is the agent-facing tool for issues, PRs, releases and API calls.
# GITHUB_TOKEN is supplied by the Codex environment and is never persisted here.
if ! command -v gh >/dev/null 2>&1; then
  GH_VERSION="${GH_VERSION:-2.80.0}"
  case "$(uname -m)" in
    x86_64|amd64) GH_ARCH=amd64 ;;
    aarch64|arm64) GH_ARCH=arm64 ;;
    *) echo "Unsupported architecture for GitHub CLI: $(uname -m)" >&2; exit 1 ;;
  esac
  GH_TMP="$(mktemp -d)"
  trap 'rm -rf "$GH_TMP"' EXIT
  curl --fail --location --retry 3 \
    "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${GH_ARCH}.tar.gz" \
    -o "$GH_TMP/gh.tar.gz"
  tar -xzf "$GH_TMP/gh.tar.gz" -C "$GH_TMP"
  install -m 755 "$GH_TMP/gh_${GH_VERSION}_linux_${GH_ARCH}/bin/gh" "$BIN_DIR/gh"
fi

gh --version
# Setup-only GitHub/GPG/SSH credentials must be installed after gh is present;
# Codex removes their environment variables before the interactive agent phase.
# shellcheck disable=SC1091
source scripts/codex_agent_credentials.sh
if test -n "${CODEX_GITHUB_TOKEN:+configured}${GITHUB_TOKEN:+configured}"; then
  gh auth status >/dev/null
fi
if ! command -v cloud >/dev/null 2>&1; then
  bash scripts/install_cloud_cli.sh
fi
cloud configure set --cli-agree-privacy-statement=true

echo "Codex repository environment ready"
echo "Maintenance installed: $INSTALL_DIR/codex_maintenance.sh"
