#!/usr/bin/env bash
set -euo pipefail
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# A resumed cached container must remain maintainable even with no GitHub
# connectivity. Refresh the installed copies from the checkout when present.
for name in codex_setup.sh codex_maintenance.sh; do
  if test -f "scripts/$name"; then
    install -m 700 "scripts/$name" "$INSTALL_DIR/$name"
  fi
done

python --version | grep -q '3.14'
node --version | grep -q '^v22\.'
java -version 2>&1 | head -1 | grep -q '"25'

python -m pip install --disable-pip-version-check \
  -r requirements.txt -r requirements-dev.txt
npm install --ignore-scripts --no-audit --no-fund --package-lock=false

export PATH="$BIN_DIR:$PATH"
if ! command -v cloud >/dev/null 2>&1; then
  bash scripts/install_cloud_cli.sh
fi
cloud --version

git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.program gpg

echo "Codex cached environment refreshed"
