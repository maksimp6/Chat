# Memory DB — PyPI release gates

**Status: NOT APPROVED FOR PUBLICATION.** Candidate distribution: `alice-memory-db` version `0.1.0.dev0`; name availability on PyPI is not yet verified.

- [ ] Verify independent build from `memory-db-package/pyproject.toml` produces wheel and sdist with only `memory_engine`, README, metadata and license.
- [ ] Run `python -m twine check dist/*` and audit both archives for secrets, credentials, journals, backups and unwanted Alice code.
- [ ] Install wheel in a fresh isolated environment outside the repository; exercise `get/set/commit`, close/reopen, corruption and recovery.
- [ ] Verify source distribution builds an equivalent wheel **without relying on the repository parent directory**.
- [ ] Confirm static typing and public API have no incompatible overrides or legacy namespace signatures; independent CI all GREEN.
- [ ] Verify licensing, dependency metadata, supported Python platforms and PyPI distribution name.
- [ ] Obtain functional acceptance and separate explicit permission to publish.

Do not create an automatic upload job for pushes or pull requests. Once ready, publish only with a protected release environment and PyPI Trusted Publishing (OIDC), not stored API tokens.

The Alice application is a separate distribution. Its CI issue #1049 is not a blocker for the standalone database.
