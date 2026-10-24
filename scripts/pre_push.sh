#!/usr/bin/env bash
set -euo pipefail

echo "==> verify formatting"
if ! bash scripts/format.sh check; then
  cat >&2 <<'EOF'

Alice Pro pre-push blocked this push because canonical formatting failed.

Format explicitly with:
  bash scripts/format.sh write

Then review, stage and commit those changes before pushing again.
EOF
  exit 1
fi

echo "==> verify whitespace"
git diff --check

if ! git diff --quiet; then
  echo "pre-push gate requires a clean tracked worktree; commit or discard local changes first" >&2
  git status --short
  exit 2
fi

echo "pre-push formatting gate passed"
