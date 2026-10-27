# PR #1621: integration with the runner-only architecture

## Scope and evidence

This records source integration and conflict decisions. Subsequent isolated code
regressions ran on the existing Redmi 9 Actions runner; their exact scopes and
results are in [the validation record](pr-1621-validation/README.md). Neither
source review nor those regressions establish live application E2E.

- Original PR head: `e02750299d3292371a7f38bcb0750614117008ce`.
- Original merge base: `90a030292a8c6d2e83754bc2fd9cc5167dc32c9b`.
- Develop incorporated into this PR: `c1da112080a09c14a8dead4ea5920eccdedd5009`.
  The first integration snapshot was `64aef856ac46293d77199fd40bd681cd74a30206`;
  the subsequent `ff645a3` message usage/duration persistence and model-picker UI
  changes were also incorporated without conflicts.
- The next runner-validation stage also incorporates develop's local settings-id
  fix and provider parameter/output traces (`7ed926d`, `fd01846`, `c1da112`).
  Git's automatic merge duplicated keyword arguments in the client and mixin;
  these duplicates were removed by source review before any validation job.
- The architectural removal of the independent updater is commit
  `60f5bd7db3284575f5265206840d8746bec0bc67`, already an ancestor of that develop.
- [Issue #1620](https://github.com/maksimp6/Chat/issues/1620#issuecomment-6414683638)
  records the earlier device-side removal and delivery by the existing runner.
  That published observation is not a new independent reading of the phone.

The existing PR branch is updated without rewriting its original commits or opening
another PR. Its target remains develop; this work does not merge it into develop.
The specific instruction to reconcile the existing PR determines publication here.
Master, protections, secrets and remote-access settings are outside the change.

## Actual conflicts and architectural decisions

| Conflict | Later develop behavior | Resolution |
| --- | --- | --- |
| `scripts/redmi_app_autoupdate.py`, modified/deleted | Independent updater and boot supervisor removed by `60f5bd7`; runner deploys through `redmi_app_server.sh` | Keep deletion. Discard the old updater output/timeout workaround (batch K-2). Do not restore worker, supervisor, boot entry, local trigger or their removed tests. |
| `chat/mcp_routes.py`, photo context helper | Later server/UI changes retain photo count in `attachments.images` | Keep the bounded photo exchange context and recognize both the PR source marker and develop photo-count metadata. |
| `chat/mcp_routes.py`, user message write | Develop saves the attachment count for history reload | Keep both `source="photo"` and `attachments.images`; preserve the later UI behavior. |

The current runner workflow, controller, launcher, readiness gate, local Bash
resolution, project/file roots and subsequent frontend changes come from develop.
They are not replaced with their old PR-base versions. The initial source
reconciliation dispatched no jobs; the later authorized validation actions and
their three actual runs are documented in the separate validation record.

## Disposition of the original batches

| Batch | Retained or adjusted behavior | Verification boundary |
| --- | --- | --- |
| A | Redact free-text log messages, exceptions and repr fallbacks using existing sanitizers | Named runner regression cases passed; not a claim that every possible secret format is covered |
| B | Preserve same-key provider identity; permit replacing an expired credential; check replacement identity in the transaction; use `configured` for local-only Yandex health | No real credential replacement, revocation or provider call |
| C | Use the provider chat and stored credential; persist voice turns; keep voice UI attached to its chat and block overlap until processing ends | STT/TTS, browser microphone and physical-device behavior NOT_RUN |
| D | Store large generated images outside bounded message values; bind files to a hashed conversation namespace; publish private complete files atomically | Existing inline messages remain readable; device storage, backup and retention UNKNOWN |
| E | Separate local settings id from provider conversation id; preserve photo metadata/context and approved tool results; represent unknown summary cost as unknown | The actual client wrapper and continuation now accept the new argument; provider/model execution NOT_RUN |
| F | Treat navigation as a session write; retain truncation; block work behind a still-running timed-out session; preserve redirect method/body and existing credential boundaries | DNS lookup is not pinned to fetch and checks a single resolved address; this is not complete DNS-rebinding protection |
| G | Keep explicit workflow inputs, scalar/boolean representation, per-job RUNNER_TEMP, KiCad fingerprint inputs and environment shutdown coordination | Discard unconditional workspace pruning. Real job context and installed runner revision UNKNOWN |
| H | Recognize compact G-code; refuse unaudited relative moves, arcs and axis-coordinate resets | Real slicer compatibility and printing NOT_RUN; see limitation below |
| I | Keep existing file permissions, unlink the symlink itself while retaining leaf protection, preserve source files during ZIP work, stop commit after failed staging; leave Git in control of index locks | Keep the complete system registry with develop's existing private-tool approvals and camera path rules |
| J | Require the current nonempty claim token, resend unconfirmed results without rerunning the handler, avoid retrying timed-out handlers, omit live trace objects from durable jobs, compare dirty-file content | Both source endpoints and Python client updated together; installed client compatibility UNKNOWN; RAM-only delivery is not exactly-once |
| K | Keep applicable runner RUNNER_TEMP support through G | K-1 is superseded by updater removal; K-2 has no surviving independent-updater target |

## Functionality and data preservation review

- No age/count-only deletion of runner job workspaces is introduced. This avoids
  discarding unpublished screenshots, recordings or receipts. Existing cleanup in
  the inherited develop workflow is not re-audited or declared safe by this change.
- System tools are registered instead of silently hidden. Location, contacts, SMS
  and camera retain their existing per-tool approval metadata. Registration does
  not prove Android permissions or executables exist on the device.
- Git does not delete another process's lock and does not globally reject fetch
  or push merely because an index lock exists. Failed staging cannot commit an old index.
- Large images are private files, outside the message store size bound. There is
  no new automatic deletion policy. Their retention and inclusion in deployment
  backups remain unresolved; the earlier 98% disk observation is historical and
  current free space is unknown. Storage failure reports an error while keeping
  the provider operation id reusable; it does not start another paid generation.
- Approved tool success is retained when the optional model summary fails, using
  the existing bounded sanitized trace format. Truncation remains explicit;
  unknown cost is not reported as a free model call. The tool is not run again.
- Environment stop closes request admission before waiting for HTTP work. A
  timeout leaves storage/runtime available for retry, and deletion cannot bypass
  the registered failed runtime. Concurrent control-plane start/delete operations
  across separate runtime objects are not fully serialized by this patch.
- Claim tokens change the local-agent wire contract. Rollout must coordinate
  server and client; legacy clients without a token cannot submit a new claim's
  result. Undelivered results remain in RAM. Process loss or a gateway outage
  beyond the lease can still lose a result or allow another worker to repeat a side effect.
- The provider conversation lock is per process. It is not cross-process
  creation deduplication. Photo context is bounded textual history, not replay
  of the original image pixels to a later text request.
- A first voice turn creates a durable local chat; reconnecting SSE can replay
  the terminal signal without another provider call. A permanent connection/server
  failure may still keep the microphone busy until recovery or page reload;
  restarting the voice/model pipeline blindly is not used as recovery.
- G-code `G91`, `G2`/`G3`, and `G92` containing X/Y/Z are refused because the
  auditor does not compute their swept/relative coordinates. This is a real
  compatibility restriction, not proof those commands are intrinsically invalid.
  Plain extrusion resets (`G92 E...`) are unaffected. Removing the guard without
  implementing trajectory tracking would reinstate a misleading bounds verdict.
- Remaining controller/launcher/app version skew, rollback completeness and
  interrupted-deploy recovery concerns in #1620 are not declared fixed.

## Validation status

| Activity | Result |
| --- | --- |
| Read current AGENTS.md, PR, issue and commit history | Completed |
| Compare original PR, current develop and each conflict | Completed by source review |
| Independent reviews of messages, protocol/browser, runner/tools and lifecycle | Completed; corrections incorporated |
| Regression scenarios | Three actual runner scopes recorded; 167/169 distinct Python cases passed, two hard-link cases blocked; both JS suites passed |
| Other unit/integration/browser checks, builds and separate parse checks | NOT_RUN outside the named validation scopes |
| GitHub Runner test workflow | Full runs 40381634312 and 40382706143 failed; focused STL run 40383967914 passed only its two cases; combined status BLOCKED |
| Redmi 9 job identity/free-space observations | Recorded by the runner; see timestamped reports |
| Installed runtime/process reading, deployment, restart | NOT_RUN |
| Real provider/model/tool, live chat/voice/image E2E | NOT_RUN |
| User acceptance | Pending and separate from merge |

AGENTS.md prohibits test execution without a separate order and permits
authorized tests only inside GitHub Actions runner. The user continued the
offered runner-test stage before these jobs were dispatched. No local environment-variable
spoofing or test-guard bypass was used. Successful source reconciliation or a clean
Git mergeability result is not a VERIFIED result on the physical device.
