#!/usr/bin/env bash
# Naming, layout, reliability, type, API-error, ASVS and WCAG ratchets; see
# docs/development/{naming,reliability}.md, docs/api/errors.md,
# docs/security/asvs-l2.md and docs/frontend/accessibility.md.
# Runs with tooling only (requirements-dev.txt); tests that need the app
# run in the full suite instead.
set -euo pipefail

python -m pytest -q -p no:cacheprovider \
  tests/test_naming_conventions.py \
  tests/test_repository_root_layout.py \
  tests/test_reliability_rules.py \
  tests/test_type_checking.py \
  tests/test_api_problem_details.py \
  tests/test_asvs_checklist.py \
  tests/test_color_contrast.py
node tests/test_accessibility.js
