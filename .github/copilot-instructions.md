# Copilot instructions

- Work on a branch and open a pull request against `develop`; never push to `develop` or `master`.
- One issue, one focused pull request with deterministic regression tests.
- Before finishing, run `python -m compileall -q .`,
  `python tests/validate_runtime_modules.py` and `pytest -q`.
- Never commit secrets or add CDN-hosted UI dependencies.
