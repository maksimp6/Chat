#!/usr/bin/env bash
set -euo pipefail
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"

# Install the recovery/maintenance path first, before dependency setup can fail.
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
git fetch origin master

for name in codex_setup.sh codex_maintenance.sh; do
  git show "origin/master:scripts/$name" > "$INSTALL_DIR/$name.next"
  chmod 700 "$INSTALL_DIR/$name.next"
  mv "$INSTALL_DIR/$name.next" "$INSTALL_DIR/$name"
done

cat > "$BIN_DIR/alice-pro-maintenance.next" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME/.local/lib/alice-pro/codex_maintenance.sh"
EOF
chmod 700 "$BIN_DIR/alice-pro-maintenance.next"
mv "$BIN_DIR/alice-pro-maintenance.next" "$BIN_DIR/alice-pro-maintenance"

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
bash scripts/install_cloud_cli.sh
cloud --version

echo "Codex repository environment ready"
echo "Maintenance launcher: $BIN_DIR/alice-pro-maintenance"
echo "SSH/GPG credentials are supplied separately by Codex Environment secrets."
