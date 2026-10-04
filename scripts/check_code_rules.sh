#!/usr/bin/env bash
# Naming, layout, reliability, type, API-error, ASVS and WCAG ratchets; see
# docs/development/{naming,reliability}.md, docs/api/errors.md,
# docs/security/asvs-l2.md and docs/frontend/accessibility.md.
set -euo pipefail

python -m pytest -q -p no:cacheprovider \
  tests/test_naming_conventions.py \
  tests/test_repository_root_layout.py \
  tests/test_reliability_rules.py \
  tests/test_type_checking.py \
  tests/test_api_problem_details.py \
  tests/test_asvs_checklist.py \
  tests/test_security_headers.py \
  tests/test_color_contrast.py
node tests/test_accessibility.js
