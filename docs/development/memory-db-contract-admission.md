# Memory DB contract admission and legacy retention

## Non-negotiable invariants

- Keep `agent_memory/file_memory_db.py` byte-identical to its approved legacy baseline. No storage cutover, SQL migration, data deletion, or runtime deployment in this PR.
- Preserve the 10 approved legacy CI tests and four core-only acceptance suites. Preserve the five core fast contracts; any correction requires independent, explicit re-approval of a new SHA-256 snapshot.
- The fast lane tests **both** `FileMemoryDB` and `memory_engine.MemoryStore`; their torn-tail recovery contracts deliberately differ.
- Keep the complete pytest / application / PostgreSQL / Android / security matrix separate from fast tests.

## Fast contract enforcement

The fast job checks out an immutable policy commit by its full SHA and executes `scripts/run_fast_contracts_trusted.py` from that checkout with `python -I -S`. The trusted script:

1. Verifies pinned SHA-256 of accepted test files, the legacy source and the compatibility runner; missing or changed files fail closed.
2. Checks `tests/__init__.py` is the approved empty file and disallows extra import hooks.
3. Imports approved test files by **exact source path**, not through the mutable `tests` package, and verifies 15 exact test identities.
4. Rejects skipped, expected-failure, unexpected-success, missing or failing tests.
5. Measures import plus execution against the 2-second limit.

The original pinned `scripts/run_fast_contracts.py` also executes for backwards compatibility with accepted CI workflow contracts. A pinned parser enforces a **combined 2-second budget for both measured import-and-test intervals**, excluding Python interpreter startup, shell overhead and GitHub setup (matching the original #1104 timing scope). This temporary duplicate execution avoids weakening frozen tests; wall-clock job duration is separately visible.

To add a new contract, add a new file or reviewed test suite, update the trusted inventory and SHA-256 manifest, create a new immutable policy commit, pin it in the workflow, verify all negative tests and exact-head CI, and request independent approval. Never silently rewrite an accepted test to improve CI results.

## Security boundary that still needs repository administration

A pinned commit guards the accepted files **only when the fast workflow is actually executed**. An editable PR-head workflow is not a trusted admission controller. The active `master` ruleset currently omits `CI required` / trusted Memory DB integrity, while the `develop` integration ruleset is disabled.

Before promoting this Draft to merge-ready, enforce a trusted check whose workflow and reference policy cannot be altered by the candidate PR, bind it to the PR's exact head SHA, and require its GitHub App/source in native rulesets for `develop` and `master`. Add regression evidence: removing the candidate's fast job or altering test import hooks must block merge admission. Do not introduce a mandatory human review count while only one contributor has access.

The PR is stacked on #1054. Rebase/rebuild on the canonical parent only after its integration ancestry is reconciled; do not merge unrelated develop changes into this Draft. Re-run every required check on the resulting exact SHA.

No merge and no migration are authorized by this document.
