#!/usr/bin/env bash
# Naming, repository layout, reliability and type-checking ratchets; see
# docs/development/naming.md and docs/development/reliability.md.
set -euo pipefail

python -m pytest -q -p no:cacheprovider \
  tests/test_naming_conventions.py \
  tests/test_repository_root_layout.py \
  tests/test_reliability_rules.py \
  tests/test_type_checking.py \
  tests/test_api_problem_details.py \
  tests/test_asvs_checklist.py
