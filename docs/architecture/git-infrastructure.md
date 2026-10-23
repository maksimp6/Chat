# Git infrastructure: architecture contract (Issue #1313)

Naming: "Git infrastructure" is a neutral working name. "Forge" stays reserved for the
3D printing workflow (`forge/` package, Bambu A1 mini). Do not use "Forge" for this module.

Status: proposal, not deployment authorization.

## Decision and alternatives

| Option | Benefit | Cost/risk | Decision |
| --- | --- | --- | --- |
| Forgejo | Open source, established Git hosting, PRs, issues and API | External application and update lifecycle | Baseline to compare and interoperability target |
| Gitea | Mature Git hosting and web interface | External application and governance dependency | Alternative baseline |
| GitLab | Full lifecycle and CI | High operational footprint for a minimal personal service | Not the first deployment target |
| New Git implementation | Complete control | Protocol, object integrity, security and maintenance risk | Rejected |
| Thin Git service around existing Git | Small, independently deployable modules | Requires implementing authorization and collaboration | Chosen incremental research direction |

No product selection or cutover is authorized by this document.

## Boundary

Use standard Git object storage and a supported Git executable for protocol handling.
Do not implement Git packfiles, hashing, negotiation, or object parsing from scratch.
The first vertical slice is a **read-only repository explorer** over a test bare
repository. Git write access, public exposure, authentication, and production
migration are separate security-reviewed stages.

Proposed components:

1. **Repository adapter:** canonical allowlisted repository root, read-only
   access to a bare test repository, bounded Git subprocesses without a shell.
2. **HTTP API:** repository metadata, refs, commit history, tree and blob
   metadata, with strict size and pagination limits.
3. **Local web UI:** browse branches, files and commits; bundle static assets.
4. **Policy service (later):** authenticated principals, ACLs, protected refs,
   audit journal and server-side push authorization.
5. **Collaboration service (later):** issues, discussions, PR reviews and
   check status; metadata must be exportable independently of GitHub.
6. **Job service (later):** isolated runners, explicit resource budgets,
   immutable job inputs and verified result provenance.

Git history alone does not preserve GitHub issues, reviews, attachments, rulesets,
secrets or Actions artifacts. Migration must inventory each class separately.

## Threats and non-goals

Threats: path traversal, symlink escapes, command injection, malicious Git
objects, resource exhaustion, unauthorized ref updates, forged CI status,
credential leakage, and backup corruption. No network listener should be
exposed beyond localhost until authentication, rate limits and review exist.

Not in first slice: custom Git protocol, GitHub API parity, multi-tenant hosting,
production data imports, remote push, CI execution or GitHub replacement.

## Acceptance and verification matrix

| Check | Required evidence | Current status |
| --- | --- | --- |
| Browse refs, tree and commits | Fixture bare repo, deterministic API assertions | NOT_RUN |
| Reject traversal and symlink escape | Negative control that fails without guard | NOT_RUN |
| Bound blob size and Git subprocess time | Deterministic oversized/timeout fixtures | NOT_RUN |
| Confirm no writes to bare repo | Before/after refs and object inventory | NOT_RUN |
| Clone/fetch/push | Independent Git client E2E on isolated repo | NOT_RUN |
| PR/review/check/merge | Independent user-flow E2E, negative authorization | NOT_RUN |
| Backup/restore | Restore into clean instance and compare data | NOT_RUN |
| Android rendered interface | Real device/browser evidence | NOT_RUN |

PASS requires observed assertions and negative controls. SKIP, timeout, ERROR
and NOT_RUN are never reclassified as PASS.

## Execution sequence

1. Accept or amend this contract and identify an implementation owner.
2. Add a minimal read-only Git adapter and isolated fixture tests in a new
   implementation PR to `develop`.
3. Add API and local UI, then security review and real E2E.
4. Implement protected writes, collaboration, jobs and migration in separate PRs.
5. Evaluate Forgejo/Gitea operational baseline before authorizing any cutover.

Existing GitHub hosting, `develop`, `master` and production infrastructure
remain unchanged. No merge, deployment, or destructive operation is authorized.
