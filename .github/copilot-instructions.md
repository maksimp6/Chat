# Copilot instructions

Follow [`AGENTS.md`](../AGENTS.md): repository workflow, architecture rules and
validation commands apply to Copilot as to every other agent.

- Work on a branch and open a pull request; never push to `master`.
- One issue, one focused pull request with deterministic regression tests.
- Before finishing, run `python -m compileall -q .`,
  `python tests/validate_runtime_modules.py` and `pytest -q`, and
  `bash scripts/format.sh check` for formatting.
- Never commit secrets or add CDN-hosted UI dependencies.
