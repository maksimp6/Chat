#!/usr/bin/env bash
set -euo pipefail

repo="${1:-/workspace/chat-chrome-command}"
venv="${ALICE_RDC_DEV_VENV:-/workspace/.dev-venv}"

test -f "$repo/requirements-dev.txt"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$venv/bin/python" -m pip install --disable-pip-version-check -r "$repo/requirements-dev.txt"

printf 'RDC dev environment ready: %s\n' "$venv"
printf 'Use: %s/bin/python -m pytest\n' "$venv"
