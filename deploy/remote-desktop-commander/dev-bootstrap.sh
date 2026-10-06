#!/usr/bin/env bash
set -euo pipefail

repo="${1:-/workspace/chat-chrome-command}"
venv="${ALICE_RDC_DEV_VENV:-$repo/.venv}"

test -d "$repo/.git" || test -f "$repo/.git"
test -f "$repo/requirements.txt"
test -f "$repo/requirements-dev.txt"
test -f "$repo/package.json"
test -f "$repo/app.py"
test -f "$repo/scripts/pre_push.sh"

python_version="$(python --version 2>&1)"
case "$python_version" in
  "Python 3.14."*) ;;
  *)
    printf 'Alice requires the CI Python 3.14 line; got: %s\n' "$python_version" >&2
    exit 2
    ;;
esac

python -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$venv/bin/python" -m pip install --disable-pip-version-check \
  -r "$repo/requirements.txt" \
  -r "$repo/requirements-dev.txt"

(
  cd "$repo"
  npm_config_cache="${ALICE_RDC_NPM_CACHE:-/tmp/alice-npm-cache}"     npm install --ignore-scripts --no-audit --no-fund --package-lock=false
)

bin_dir="$HOME/.local/bin"
mkdir -p "$bin_dir"
export PATH="$bin_dir:$HOME/yandex-cloud/bin:$PATH"

# GitHub CLI: pinned to the version already required by repository bootstrap CI.
if ! command -v gh >/dev/null 2>&1; then
  gh_version="2.96.0"
  case "$(uname -m)" in
    x86_64|amd64) gh_arch=amd64 ;;
    aarch64|arm64) gh_arch=arm64 ;;
    *) printf 'Unsupported architecture for GitHub CLI: %s
' "$(uname -m)" >&2; exit 2 ;;
  esac
  gh_tmp="$(mktemp -d)"
  curl --fail --location --retry 3     "https://github.com/cli/cli/releases/download/v${gh_version}/gh_${gh_version}_linux_${gh_arch}.tar.gz"     -o "$gh_tmp/gh.tar.gz"
  tar -xzf "$gh_tmp/gh.tar.gz" -C "$gh_tmp"
  install -m 755 "$gh_tmp/gh_${gh_version}_linux_${gh_arch}/bin/gh" "$bin_dir/gh"
  rm -rf "$gh_tmp"
fi
gh --version | head -n 1 | grep -q 'gh version 2.96.0'

# Cloud.ru EDS: repository-owned installer verifies the pinned release checksum.
python "$repo/scripts/install_eds.py" --bin-dir "$bin_dir"
eds version >/dev/null

# Cloud.ru general CLI: repository-owned mirror with pinned SHA-256 verification.
CLOUD_CLI_INSTALL_DIR="$bin_dir" bash "$repo/scripts/install_cloud_cli.sh"
cloud --version >/dev/null 2>&1 || cloud version >/dev/null

# Yandex Cloud CLI: official non-interactive installer, credentials remain external.
if ! command -v yc >/dev/null 2>&1; then
  yc_install="$(mktemp)"
  curl --fail --location --retry 3     https://storage.yandexcloud.net/yandexcloud-yc/install.sh     -o "$yc_install"
  bash "$yc_install" -i "$HOME/yandex-cloud" -n
  rm -f "$yc_install"
fi
yc version >/dev/null

# Prove the same local gate used before publishing is executable in this image.
(
  cd "$repo"
  PATH="$venv/bin:$PATH" bash scripts/format.sh check
  git diff --check
)

# Importing app.py initializes the lightweight local state and all blueprints.
# Use an isolated path so bootstrap never mutates a user's existing Alice data.
smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
(
  cd "$repo"
  export ALICE_DB_PATH="$smoke_dir/alice-rdc-smoke.db"
  PATH="$venv/bin:$PATH" "$venv/bin/python" -c \
    'import app; assert app.app.test_client().get("/healthz").status_code == 200'
)

printf 'Alice RDC development environment ready\n'
printf 'Repository: %s\n' "$repo"
printf 'Python: %s\n' "$("$venv/bin/python" --version 2>&1)"
printf 'Node: %s\n' "$(node --version)"
printf 'Pre-push gate: PATH=%s/bin:$PATH bash scripts/pre_push.sh\n' "$venv"
printf 'Run Alice: cd %s && ALICE_DB_PATH=/workspace/alice-dev.db %s/bin/python app.py\n' "$repo" "$venv"