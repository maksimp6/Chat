# #538 fixed-fixture benchmark handoff

Canonical issue: https://github.com/maksimp6/Chat/issues/538.
Accepted scope: the issue's remaining full/incremental benchmark and the bounded
fixture request in issuecomment-5992155072. Query #834 and revision/reuse #950 are
already present; do not implement them again.

Stage: regression contract → implementation → exact-head validation → draft PR.
Primary owner: Codex, Backend/Test Engineer, using the existing shell checkout and
GitHub connector. No extra model worker or paid provider invocation.
Branch: `issue-538-index-benchmark`, based on master
`51aaf8273f4f465fe78aa0a79e76a22d8f141e35`.

Artifact: an adjacent local benchmark CLI, deterministic regression tests and the
existing repository-index runbook. Fixed synthetic Git snapshots only; compare
full, unchanged-cache, changed-cache and repeated query work; verify equivalence,
input immutability and provenance before reporting timings. Keep measurements
separate from correctness gates and do not advertise a latency SLA.

Authorization: the owner's repository-completion request covers this reversible
development slice and publication. No deployment, protected-branch write, secret,
printer/device action, migration, model request or automatic merge.
Pause on a changed contract/base, failed correctness/format check, unsafe output,
or missing publication capability. Broader #538 remains open after this slice.
