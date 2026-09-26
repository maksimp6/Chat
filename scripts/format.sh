#!/usr/bin/env bash
set -euo pipefail

mode="${1:-write}"

case "$mode" in
  write)
    python -m ruff format .
    npx --yes prettier@3.6.2 --write "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" ".github/**/*.yml" "*.md"
    ;;
  check)
    python -m ruff format --check .
    npx --yes prettier@3.6.2 --check "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" ".github/**/*.yml" "*.md"
    ;;
  *)
    echo "Usage: $0 [write|check]" >&2
    exit 2
    ;;
esac
