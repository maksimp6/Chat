# External RDC call budget

Refs #650 and #1131; on-demand observation belongs to #1088. This is an
external connector budget, separate from Logos's Cloud.ru service health.

## Evidence, 2026-10-11

The owner-provided Desktop Commander email was verified: 13:39:11 UTC,
80% of 10,000 monthly free calls used; service stops at the cap and resets
at the beginning of the next month. A subsequent account read reported
19% remaining. The percentage is not an exact call count or reset timezone.

Device-local summaries (different retention/session windows, **not** the
account's complete monthly billing ledger):

| Device | Local calls | start_process | read_process_output | Failures |
| --- | ---: | ---: | ---: | ---: |
| Redmi 9 | 5309 | 3043 | 1530 | 4 |
| Cloud RDC | 502 | 268 | 184 | 8 |
| Redmi 15 | 59 | 28 | not in top five | 28 |

The latest 100 Redmi 9 history records include 11 empty output reads.
One bounded 12-second Perfetto recording was polled twice with no output
before a third read returned its result. Cloud RDC's latest 100 records
include one empty read and several 2–3-second partial-output polls for tests
that completed in about 9 seconds. Partial output is not automatically waste:
interactive or urgent diagnostics may require it.

Redmi 15 history includes repeated process-launch failures and a concrete
`spawn ... sh EACCES`. Changing shell command text does not repair a denied
shell executable. Do not keep probing the same failure without a diagnosed
environment change.

No screenshot command appears in these recent samples. The inspected
device-local `android_actions.py` captures only for its explicit `screenshot`
command, and the OCR/diff helpers consume existing images. No continuous
capture process was found by a targeted process-name check; this is not a
complete forensic inventory. Do not disable device services based on suspicion.

## Required work pattern

1. Choose a bounded allowance before RDC work (normally 10 calls for a small
   diagnostic task). Count failed calls as spent; do not treat an empty output
   or timeout as permission to resubmit a mutation. Reuse the current device
   list, stats and relevant output instead of rereading them for reassurance.
2. Batch independent safe inspection commands. Use the local checkout for
   Git, compilation and tests when device execution is unnecessary. Do not
   combine consequential actions to bypass their approvals.
3. Keep the original device/PID/call_id and invocation/trace correlation.
   Remote `start_process` is capped at 3 seconds even if timeout_ms is larger.
   Delay the first read until useful output is expected (15 seconds for a
   known 12-second capture); then back off 5, 10, 20, 40 seconds by default,
   at most four reads within 60 seconds and the task's remaining allowance.
   Stop with the original handle if pending. Do not kill/restart or relaunch
   the process to obtain its result. A transport failure is propagated, never
   blindly retried. Waiting does not consume a connector call.
4. Capture only for an explicit observation. Reuse a result only for the
   same owner/device/invocation and observation ID, at most two seconds by
   default (five seconds maximum). Request a new ID for fresh evidence;
   invalidate after input, focus/geometry/rotation change, including an
   ambiguous action outcome. A TTL alone does not prove the screen unchanged.
5. A denied executable, authentication failure, quota exhaustion or unsupported
   operation stops that path. Diagnose once through a different supported
   inspection path; a further attempt needs changed prerequisites. Preserve
   RDC, user apps and active measurements. Never subscribe, restore Cloud.ru
   containers or start paid resources to bypass the external limit.

## Executable boundary and limitations

`rdc_connection.usage_control.RdcTask` wraps already-authorized callbacks;
it reserves each call before execution, coalesces concurrent observation reads,
invalidates on mutation, and bounds collection with delay/deadline/poll/call
limits. It never submits a job itself or retries mutations. Callbacks retain
UniversalToolExecutor, InvocationContext, ExecutionTrace and transport timeouts.
Return immutable observation references; do not mutate a cached result.

The current master has no Python client that calls the external RDC connector.
These confirmed polls originate in assistant work sessions; the repository
guidance controls that workflow, while the helper is available for managed
Python orchestration. It is not wired into ChatGPT's connector transport and
cannot stop another chat/client or enforce the shared monthly cap. No deployed
Android/Cloud.ru runtime is changed in this slice.

## Validation

`python -m pytest -q tests/test_rdc_usage_control.py tests/test_rdc_pairing.py`
checks unchanged pairing, callable device actions, failed/ambiguous actions
executed once, failure accounting, concurrent capture reuse, freshness and
device isolation, fresh observations, invalidation, finite poll/deadline/call
limits, completed-result reuse and a synthetic 12-second recording requiring
one delayed result read rather than two empty reads plus a final read.

Device access was inspected read-only through the existing Redmi 9 RDC agent;
no restart, deployment, screenshot or input action was required. Unit tests do
not establish a production cutover or account-wide savings measurement.
