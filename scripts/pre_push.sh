#!/usr/bin/env bash
set -euo pipefail

mode="${1:-changed}"

echo "==> format repository"
bash scripts/format.sh write

echo "==> verify formatting"
bash scripts/format.sh check

echo "==> compile tracked Python"
python -m compileall -q .

echo "==> validate frontend modules"
python scripts/validate_frontend_modules.py

echo "==> validate agent skills"
python scripts/validate_agent_skills.py

if [[ "$mode" == "full" ]]; then
  echo "==> full Python regression suite"
  pytest --durations=30 -q
else
  echo "==> CI/workflow contract tests"
  pytest -q \
    tests/test_ci_efficiency_contract.py \
    tests/test_ci_environment.py \
    tests/test_ci_image_workflow.py \
    tests/test_formatter_contract.py \
    tests/test_merge_readiness_workflow_contract.py
fi

if ! git diff --check; then
  echo "pre-push gate failed: whitespace errors remain" >&2
  exit 1
fi

if ! git diff --quiet; then
  echo "pre-push gate formatted or modified tracked files; review and stage them before publishing" >&2
  git status --short
  exit 2
fi

echo "pre-push gate passed"
