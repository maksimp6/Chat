#!/usr/bin/env bash
set -euo pipefail

mode="${1:-write}"
DOC_GLOBS=("*.md" "docs/**/*.md" ".github/ISSUE_TEMPLATE/**/*.md" ".agents/**/*.md")
resolve_prettier() {
  if [[ ! -x "./node_modules/.bin/prettier" ]]; then
    echo "Prettier is not installed. Run: npm install --ignore-scripts --no-audit --no-fund --package-lock=false" >&2
    exit 2
  fi
  PRETTIER_CMD=("./node_modules/.bin/prettier")
}

run_prettier() {
  resolve_prettier
  "${PRETTIER_CMD[@]}" "$@"
}

case "$mode" in
  write)
    python -m ruff format .
    run_prettier --write "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" "${DOC_GLOBS[@]}"
    ;;
  docs-write)
    run_prettier --write "${DOC_GLOBS[@]}"
    ;;
  docs-check)
    run_prettier --check "${DOC_GLOBS[@]}"
    ;;
  check)
    python -m ruff format --check .
    run_prettier --check "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" "${DOC_GLOBS[@]}"
    ;;
  *)
    echo "Usage: $0 [write|check|docs-write|docs-check]" >&2
    exit 2
    ;;
esac
