#!/usr/bin/env bash
set -Eeuo pipefail
printf '[codex-maintenance] started\n' >&2
PS4='+ [${SECONDS}s] maintenance:${LINENO}: '
set -x
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
cd "$REPO_ROOT"

log() {
  printf '\n[codex] %s\n' "$*" >&2
}

run_timed() {
  local limit="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout --foreground "$limit" "$@"
  else
    "$@"
  fi
}

install_github_cli() {
  if command -v gh >/dev/null 2>&1; then
    gh --version
    return
  fi

  local version="${GH_VERSION:-2.80.0}"
  local arch
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *)
      echo "Unsupported architecture for GitHub CLI: $(uname -m)" >&2
      return 1
      ;;
  esac

  local tmp
  tmp="$(mktemp -d)"
  log "Installing GitHub CLI v$version for $arch"
  run_timed 180s curl \
    --fail \
    --location \
    --retry 2 \
    --retry-delay 2 \
    --connect-timeout 10 \
    --max-time 150 \
    "https://github.com/cli/cli/releases/download/v${version}/gh_${version}_linux_${arch}.tar.gz" \
    -o "$tmp/gh.tar.gz"

  tar -xzf "$tmp/gh.tar.gz" -C "$tmp"
  install -m 755 "$tmp/gh_${version}_linux_${arch}/bin/gh" "$BIN_DIR/gh"
  rm -rf "$tmp"
  gh --version
}

configure_github() {
  log "Configuring GitHub CLI over HTTPS"
  gh config set git_protocol https

  if test -z "${GITHUB_TOKEN:-}"; then
    echo "[codex] ERROR: GITHUB_TOKEN is not set" >&2
    return 1
  fi

  run_timed 30s gh auth status --hostname github.com

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git remote get-url origin >/dev/null 2>&1; then
      git remote set-url origin "https://github.com/maksimp6/Chat.git"
    else
      git remote add origin "https://github.com/maksimp6/Chat.git"
    fi
    gh auth setup-git
  fi
}

log "Refreshing the two installed control scripts from the current checkout"
for name in codex_setup.sh codex_maintenance.sh; do
  if test -f "scripts/$name"; then
    install -m 700 "scripts/$name" "$INSTALL_DIR/$name"
  fi
done

log "Checking runtime versions"
python --version
node --version
java -version
python --version | grep -q '3.14'
node --version | grep -q '^v22\.'
JAVA_VERSION="$(java -version 2>&1)"
grep -q '"25' <<<"$JAVA_VERSION"

log "Refreshing Python dependencies (max 10 minutes)"
run_timed 10m python -m pip install --disable-pip-version-check \
  -r requirements.txt -r requirements-dev.txt

log "Refreshing Node dependencies (max 10 minutes)"
run_timed 10m npm install --ignore-scripts --no-audit --no-fund --package-lock=false

export PATH="$BIN_DIR:$PATH"

log "Preparing GitHub CLI"
install_github_cli
configure_github

log "Preparing Cloud.ru CLI"
if ! command -v cloud >/dev/null 2>&1; then
  run_timed 180s bash scripts/install_cloud_cli.sh
fi
test -x "$BIN_DIR/cloud"
run_timed 30s cloud --version

git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.program gpg

log "Codex cached environment refreshed"
