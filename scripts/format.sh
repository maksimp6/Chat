#!/usr/bin/env bash
set -euo pipefail

mode="${1:-write}"

case "$mode" in
  write)
    python -m ruff format .
    ;;
  check)
    python -m ruff format --check .
    ;;
  *)
    echo "Usage: $0 [write|check]" >&2
    exit 2
    ;;
esac
