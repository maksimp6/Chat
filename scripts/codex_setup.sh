#!/usr/bin/env bash
set -euo pipefail
umask 077

python --version | grep -q '3.14'
node --version | grep -q '^v22\.'
java -version 2>&1 | head -1 | grep -q '"25'

python -m pip install --disable-pip-version-check \
  -r requirements.txt -r requirements-dev.txt

npm install --ignore-scripts --no-audit --no-fund --package-lock=false

mkdir -p "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.local/bin"
chmod 700 "$HOME/.ssh" "$HOME/.gnupg"

git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global gpg.program gpg

if [ -x scripts/install_cloud_cli.sh ]; then
  scripts/install_cloud_cli.sh
fi

echo "Codex repository environment ready"
echo "SSH/GPG credentials are supplied separately by Codex Environment secrets."
