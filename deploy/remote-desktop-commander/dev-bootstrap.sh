#!/usr/bin/env bash
set -euo pipefail

repo="${1:-/workspace/chat-chrome-command}"
venv="${ALICE_RDC_DEV_VENV:-$repo/.venv}"

test -d "$repo/.git" || test -f "$repo/.git"
test -f "$repo/requirements.txt"
test -f "$repo/requirements-dev.txt"
test -f "$repo/package.json"
test -f "$repo/app.py"
test -f "$repo/scripts/pre_push.sh"

python_version="$(python --version 2>&1)"
case "$python_version" in
  "Python 3.14."*) ;;
  *)
    printf 'Alice requires the CI Python 3.14 line; got: %s\n' "$python_version" >&2
    exit 2
    ;;
esac

python -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$venv/bin/python" -m pip install --disable-pip-version-check   -r "$repo/requirements.txt"   -r "$repo/requirements-dev.txt"

(
  cd "$repo"
  npm install --ignore-scripts --no-audit --no-fund --package-lock=false
)

# Prove the same local gate used before publishing is executable in this image.
(
  cd "$repo"
  PATH="$venv/bin:$PATH" bash scripts/format.sh check
  git diff --check
)

# Importing app.py initializes the lightweight local state and all blueprints.
# Use an isolated path so bootstrap never mutates a user's existing Alice data.
smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
(
  cd "$repo"
  export ALICE_DB_PATH="$smoke_dir/alice-rdc-smoke.db"
  PATH="$venv/bin:$PATH" "$venv/bin/python" -c     'import app; assert app.app.test_client().get("/healthz").status_code == 200'
)

printf 'Alice RDC development environment ready\n'
printf 'Repository: %s\n' "$repo"
printf 'Python: %s\n' "$("$venv/bin/python" --version 2>&1)"
printf 'Node: %s\n' "$(node --version)"
printf 'Pre-push gate: PATH=%s/bin:$PATH bash scripts/pre_push.sh\n' "$venv"
printf 'Run Alice: cd %s && ALICE_DB_PATH=/workspace/alice-dev.db %s/bin/python app.py\n' "$repo" "$venv"
