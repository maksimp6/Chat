#!/usr/bin/env bash
set -euo pipefail

# alice-pro-canonical-format-hook
root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Alice Pro pre-commit: not inside a Git working tree." >&2
  exit 2
}

cd "$root"

if ! bash scripts/format.sh check; then
  cat >&2 <<'EOF'

Alice Pro pre-commit blocked this commit because canonical formatting failed.

Fix it with:
  bash scripts/format.sh write

Then review and stage the formatter changes before committing again.
EOF
  exit 1
fi
