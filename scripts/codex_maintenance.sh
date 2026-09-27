#!/usr/bin/env bash
set -euo pipefail
umask 077

INSTALL_DIR="$HOME/.local/lib/alice-pro"
BIN_DIR="$HOME/.local/bin"

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

# Refresh installed control scripts from origin/master for the next run.
git fetch origin master
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
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

echo "Codex cached environment refreshed"
