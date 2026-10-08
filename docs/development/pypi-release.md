# Alice Pro — PyPI release preparation

Status: **NOT READY FOR PUBLICATION**. This branch defines package metadata and tests, but the `alice_pro` package and production entrypoint are not implemented yet.

Distribution candidate: `maksimp6-alice-pro` (PyPI name availability **not verified**).
Import name: `alice_pro`. CLI: `alice-pro`.
Version: `0.1.0.dev0`; this is not a stable release.

## Release blockers

1. Implement `alice_pro/__init__.py`, `alice_pro/__main__.py` and `main()` without launching services on import; preserve existing application startup and resource loading.
2. Build a wheel and sdist; ensure only approved Python package files and documentation ship. No database files, credentials, private keys, `.env`, traces, logs, tests with secrets or user data.
3. Install the built wheel in a **fresh isolated environment**, outside the repository, and verify `python -m alice_pro --help` and `alice-pro --help`. Test actual application startup and exit, not just help.
4. Validate metadata and archives using `twine check dist/*`; inspect contents and declared runtime dependencies. Confirm distribution name is available on PyPI.
5. Require exact-head package tests, static checks, security review, license review, dependency audit and functional acceptance. Publication requires a separate explicit owner decision.

## Local release-candidate commands

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m zipfile -l dist/*.whl
```

Never publish from a pull request or a normal push. When all gates are verified, create a separate release workflow using PyPI Trusted Publishing (OIDC), a protected GitHub environment, pinned actions and explicit release-tag/manual approval. No long-lived PyPI token should be stored in the repository.

The standalone Memory DB is a separate distribution; do not bundle its implementation into the Alice wheel.
