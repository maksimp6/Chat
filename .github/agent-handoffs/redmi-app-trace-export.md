# Redmi application traces: prepared encrypted archive extension

Verified develop: `9508e7e8c25ba158febc046cfb67399bbd33daed` (2026-10-25).
Initial audit used `a806241cd545f8625a881a6c25797a89be596094`; subsequent develop
changes affected screenshot evidence/capture only. Export sources, instructions
and workflow wiring were rechecked unchanged, and the task branch fast-forwarded.
Branch: `fix/encrypted-app-trace-export`. This report expires on the next develop push.

Canonical task: owner delegation from conversation
`01a1713f-cd39-710a-ac3e-92b4e2c0b02f`, “all traces to GitHub LFS”, with explicit
no local scripts/tests, no device/model/capture runs, no credentials inspection,
no new access/keys, no source deletion, no merge/deploy, and separate approval
before real-data publication. These instructions are the accepted scope for code
preparation, not authorization to activate export. Owner: Codex, Infra/Backend
implementation role; tools: shell file/git operations and GitHub read APIs.
Skills read: issue-to-pr and security-review. Stage: code preparation, NOT_RUN.

## Existing implementation and change

There are five workflows. None calls `redmi_android_diagnostics.sh` or
`redmi_publish_diagnostics.sh`. The runner publishes job/step logs to Actions
Results. The screenshot publisher has its own fixed artifact list; it is not a
general ExecutionTrace export. No duplicate open export PR was found; #1616
changes chat trace references and #1497 serialization error handling, not export.

This change extends the existing Android diagnostics archive and publisher only.
The collector adds all retained `runtime_invocation:` records from one verified
native Kristall journal view, resolves trace/result/error chunks through the
existing reader, and retains the stored values without another sanitization pass.
It neither opens a second Kristall writer nor stores a raw database copy. Native
replay necessarily inspects the whole journal in memory; only invocation records
are written to the private archive inputs, never unrelated credentials records.
This is a privacy review item before execution, not a claim that the journal is
free of credentials.

The encrypted inner manifest contains counts, status distribution, empty-trace
count, created-at range, invocation IDs, per-file sizes/hashes and journal
sequence/digest. `system.jsonl` and every existing rotation `.1`–`.3`, plus
`backend.log`, are included byte for byte. Missing current logs, torn journal,
missing/corrupt referenced chunks, unexpected rotations, or concurrent mutation
fail the snapshot. Partial output remains private; no success manifest is written.
An existing attempt is never overwritten. A zero-invocation snapshot and empty
traces are reported explicitly, not represented as proof of full trace capture.

The outer public manifest adds only `app_trace_exit`; identities/content/counts
remain inside age encryption. The publisher refuses unsuccessful or legacy
archives lacking app status. Existing ciphertext SHA verification, fresh remote
LFS download, and decrypted byte comparison remain mandatory. No new keys,
permissions, storage engine, uploader, scheduler or workflow is introduced.

## Exact coverage limits and activation blockers

- Only currently retained invocation records, including created/running/failed/
  cancelled/completed states. Not earlier revisions of those records, orphan
  chunks, deleted invocations, or other runtime memory files.
- Sanitization/truncation already performed upstream is irreversible. Export does
  not claim the original model/tool payload is intact.
- Logs include early API/startup failures only if the current logging code wrote
  them. Non-persisted `traced_operation` results, browser-only errors and lost
  rotations are not recovered. Screenshots/MP4/events and Actions Results remain
  outside this archive extension.
- Journal and logs have separate snapshot boundaries. Continuous writes may
  cause explicit failure; there is no automatic retry/recovery scheduler. A later
  authorized run attempt repeats a full retained snapshot, with no deletion or
  cursor advancement. This is not a continuous no-loss guarantee.
- Automatic workflow integration is **NOT_IMPLEMENTED**: current deploy job has
  `permissions: {}` and enabling contents-write is outside the no-new-access
  scope. A future approved invocation must use an already authorized write job,
  exact-job token receipt from `redmi_store_github_token.sh`, and the same existing
  age identity. Do not add a long-lived token or copy credentials into artifacts.
- Scripts plus application imports must come from the same reviewed checkout;
  downloading only the collector into a temporary scripts directory is insufficient.
- Source locations/defaults require confirmation against the actual installation
  without printing secrets. No phone or real state was inspected in this task.
- Publication of real records/logs remains pending composition/privacy review and
  explicit owner approval. Encryption does not make prompts or personal data public-safe.

## Concrete verification plan (all NOT_RUN)

1. After permission to run local checks: canonical `bash scripts/format.sh write`,
   inspect changes, `bash scripts/format.sh check`, and focused
   `python -m unittest tests.test_redmi_app_trace_snapshot`. These use synthetic
   fixtures only; no providers, devices, network or credentials.
2. Expand/execute fault matrix: zero and empty traces; all invocation states;
   large multi-chunk trace/result/error; missing, corrupt and reordered chunks;
   invalid record; torn tail; journal mutation; log append/rotation during copy;
   missing logs; disk-full/timeout; repeat attempt preserving prior files.
   Require explicit failure and no publication marker for every negative case.
3. In an isolated synthetic harness, exercise existing shell capture/publish with
   mocked device commands and fake age/Git/LFS boundaries: app failure/legacy
   manifest rejects upload; missing key refuses; encryption/decryption mismatch,
   push failure, absent remote LFS object and remote digest mismatch never produce
   `published.json`. Use only fixture identities, never production secrets.
4. After separate real-data approval, inspect the private manifest and source
   mapping locally; verify counts/hash coverage including early failures, active
   invocations and rotations. Record omissions; do not print contents to Actions.
5. Only after approved workflow integration, use existing age identity and token
   receipt to publish ciphertext. Verify from a fresh LFS store and compare
   decrypted archive bytes, then verify inner file hashes and counts. Confirm a
   forced upload failure is visibly failed. No source cleanup even after success.

No tests, formatter, compile checks, repository scripts, device operations,
capture workflows, paid API calls, merge or deployment were run. Read-only source
review is not execution evidence. Publication gates remain NOT_RUN under owner
instructions; neither successful export nor end-to-end automation is claimed.
