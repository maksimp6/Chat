# RDC call-budget handoff

- Canonical scope: owner's 2026-10-11 request to investigate RDC quota waste;
  refs #650/#1131/#1088; no replacement of those Android implementation owners.
- Stage/owner: implementation and local verification, Backend Engineer/Codex.
- Accepted contract: on-demand screenshots, bounded retries and reuse, preserve
  device control; no Cloud.ru restoration or paid subscription. Owner request
  supplies this scoped contract; no privilege/approval expansion.
- Base: master 51aaf8273f4f465fe78aa0a79e76a22d8f141e35.
- Branch: fix/rdc-bounded-observation; exact head is the PR head.
- Tools: isolated real checkout, shell/pytest/formatter, GitHub connector,
  bounded read-only RDC history/stats and one targeted device inspection.
- Output: evidence-backed work policy, executable task-local callback guard,
  regression tests, draft PR. No external connector runtime interception.
- Stop if account quota is exhausted, scope/permissions change, deterministic
  device failure repeats, or callback requires privilege beyond existing policy.
- Remaining: exact-head CI and normal review; no merge/deployment. Cross-chat
  shared quota and real managed-client integration remain outside this slice.
