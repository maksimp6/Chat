#!/usr/bin/env bash
set -euo pipefail

mode="${1:-write}"
prettier_version="${ALICE_PRETTIER_VERSION:-3.6.2}"

resolve_prettier() {
  if [[ -x "./node_modules/.bin/prettier" ]]; then
    PRETTIER_CMD=("./node_modules/.bin/prettier")
    return
  fi

  if command -v prettier >/dev/null 2>&1; then
    local installed_version
    installed_version="$(prettier --version 2>/dev/null || true)"
    if [[ "$installed_version" == "$prettier_version" ]]; then
      PRETTIER_CMD=("prettier")
      return
    fi
    if [[ "$installed_version" == 3.* ]]; then
      echo "warning: using compatible global Prettier $installed_version; CI pins $prettier_version" >&2
      PRETTIER_CMD=("prettier")
      return
    fi
  fi

  # Final fallback for normal developer/CI environments with registry access.
  # Sandboxed agents can stay offline when a compatible global or local Prettier exists.
  PRETTIER_CMD=("npx" "--yes" "prettier@$prettier_version")
}

run_prettier() {
  resolve_prettier
  "${PRETTIER_CMD[@]}" "$@"
}

case "$mode" in
  write)
    python -m ruff format .
    run_prettier --write "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" "*.md"
    ;;
  check)
    python -m ruff format --check .
    run_prettier --check "static/**/*.{js,css,json}" "templates/**/*.html" "tests/**/*.js" "*.md"
    ;;
  *)
    echo "Usage: $0 [write|check]" >&2
    exit 2
    ;;
esac
