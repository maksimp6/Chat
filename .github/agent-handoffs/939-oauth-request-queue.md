# #939 / PR #948 request intake correction

Canonical issue: #939, related #906; current deliverable: PR #948.
Accepted contract: independent P1 review `PRRT_kwDOUZTa_86pvqUP` on
`dd8a2cb3962b2a2311e7e21d2fb3f6c349d19257`. Anonymous incomplete bodies must not
hold the durable OAuth mutation queue. Classify routes and accept bounded,
cancellable bodies before queue admission; revalidate state and await verified
durable commits inside the queue. Preserve refresh/replay/revocation semantics.

Owner: Codex, Backend/Test Engineer; real shell worktree and GitHub connector.
Stage: failing real-HTTP regressions → implementation → exact-head CI → review.
Branch: `issue-939-oauth-body-queue-fix`; worktree `/workspace/Chat-948`.
Prepared head: `5ffdb26de5bf147f608d9d26de845762a2f6f99a`, normal merge of current
master `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`; original head is an ancestor.

Scope: OAuth request intake/queue boundary, deterministic slow/aborted request
and durability regressions, existing acceptance runbook. Existing CI OAuth
entrypoint must import the new tests. No changes to storage format or deployment.

Authorization: owner's repository-completion request covers reversible fixes
and publication to the same PR branch. No production writes, durable mount
activation, secrets, device restart, paid model review or deployment. Preserve
the current draft and independent/live acceptance boundaries. Stop on changed
remote head, failed gate or changed contract; broader #939 remains open.
