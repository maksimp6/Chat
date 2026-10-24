#!/usr/bin/env bash
set -euo pipefail

echo "==> verify whitespace"
git diff --check

if ! git diff --quiet; then
  echo "pre-push gate requires a clean tracked worktree; commit or discard local changes first" >&2
  git status --short
  exit 2
fi

echo "pre-push gate passed"
