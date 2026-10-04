#!/usr/bin/env bash
set -euo pipefail

echo "==> format repository"
bash scripts/format.sh write

echo "==> verify formatting"
bash scripts/format.sh check

echo "==> verify whitespace"
git diff --check

if ! git diff --quiet; then
  echo "pre-push gate formatted tracked files; review and stage them before publishing" >&2
  git status --short
  exit 2
fi

echo "pre-push formatting gate passed"
