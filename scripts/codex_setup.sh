#!/usr/bin/env bash
set -euo pipefail
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# Setup runs from the checkout Codex already prepared. Network access and a
# configured Git remote are not prerequisites: install the scripts we have.
for name in codex_setup.sh codex_maintenance.sh; do
  test -f "scripts/$name"
  install -m 700 "scripts/$name" "$INSTALL_DIR/$name"
done

python --version | grep -q '3.14'
node --version | grep -q '^v22\.'
java -version 2>&1 | head -1 | grep -q '"25'

python -m pip install --disable-pip-version-check \
  -r requirements.txt -r requirements-dev.txt
npm install --ignore-scripts --no-audit --no-fund --package-lock=false

mkdir -p "$HOME/.ssh" "$HOME/.gnupg"
chmod 700 "$HOME/.ssh" "$HOME/.gnupg"

git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.program gpg

export PATH="$BIN_DIR:$PATH"
if ! command -v cloud >/dev/null 2>&1; then
  bash scripts/install_cloud_cli.sh
fi
cloud --version

echo "Codex repository environment ready"
echo "Maintenance installed: $INSTALL_DIR/codex_maintenance.sh"
