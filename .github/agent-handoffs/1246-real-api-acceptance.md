# #1246 real API acceptance

Accepted contract: owner's 2026-10-15 request for a separate real test type that
adds an actual key, checks an actual response and keeps code/state/keys protected.
Canonical issue #1246 also records the admission and current Android failure.

Owner: this Codex session, Test Engineer; shell/GitHub/RDC tools, no subagents.
Stage: implementation → deterministic guard checks → available live acceptance
→ review → draft PR. Branch `test/1246-real-api-acceptance`, base develop
`50446b70ceac2913a15e5b84410e668d42ee0a4f`. All ordinary PRs target develop.

Reuse `scripts/local_launch_smoke.py` helpers, not a new provider or credential
store. Outputs: opt-in suite, guarded private-key import, real response/history/
Trace/restart predicates, offline safety regressions, private evidence and docs.
No mocks may produce live PASS. No plaintext API key in Git/log output/evidence.

No production data changes, Android key replacement, master writes, merge, RDC
restart, new cloud services or recurring paid jobs. One explicit invocation is
bounded to one short response, but not a guaranteed currency ceiling. Keys stay
on their existing machine. Pause live execution on absent permissions/secrets,
unsafe storage, changed target/head, missing approved spend or unavailable device.
Report a blocked/red live result separately from green deterministic guard tests.

CODEOWNERS declares owner review; it does not prove enforcement on develop.
The installed Android APK/UI still requires its separate #1246 acceptance.
