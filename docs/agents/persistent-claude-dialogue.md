# Persistent Claude dialogue

This runbook covers the first dialogue slice of [issue #703](https://github.com/maksimp6/Chat/issues/703), developed in [PR #716](https://github.com/maksimp6/Chat/pull/716). It is a bounded, tool-free conversation runner. A genuine Claude CLI continuation across hosted workers remains a live acceptance check; synthetic transcript tests alone do not prove it.

## Start and continue

After the workflow change is merged, an authorized repository writer starts a session with a new issue comment whose first nonblank text is:

```text
@claude-lite session Remember the phrase “orange lighthouse”. Explain how this session sleeps.
```

Before its first paid turn, CI records a trusted started marker and then adds the `claude-session` label to the issue. Subsequent comments from authorized writers in that issue are dialogue commands without another mention:

```text
What phrase did I ask you to remember?
```

The label routes comments; it does not grant permission. The worker also checks the author's current repository write permission. Bot replies, duplicate source comment IDs, checkpoint metadata, and comments from unauthorized authors do not dispatch Claude. The existing ordinary Claude-Lite task route excludes session commands and labeled session issues so that one comment does not start two workers.

Each eligible comment enters the durable queue in source comment order. The initial worker also collects authorized plain follow-ups posted while its first CI job was queued. A new comment during a turn does not cancel or restart that turn. The active worker checks for subsequent comments at a safe turn boundary; another CI wake restores the latest committed checkpoint when needed. The worker drains a bounded batch and then pauses. There is no paid polling loop to keep a provider cache warm.

## Sleep, restore, and context

Normal turn completion sets the session to `sleeping` with reason `awaiting_comment`. The Actions process may end, but the logical session and its native Claude history remain open. The next eligible comment starts CI, restores the checkpoint in a private configuration directory and the compatible workspace, and resumes the same native session.

The checkpoint contains the durable goal, work-item links, role and role epoch, event deduplication, pending commands, results, and the selected native JSONL transcript. It does not restore credentials, settings, hooks, plugins, MCP configuration, or arbitrary files. Only an encrypted checkpoint is uploaded to Actions artifacts. Artifact retention is finite; expiration, deletion, a changed encryption credential, or an unavailable transcript blocks recovery instead of silently starting a new conversation.

The public checkpoint marker identifies the logical and native sessions, generation, completed-comment watermark, workflow run, exact head, artifact, and ciphertext digest. These are recovery evidence, not the private conversation. Restore requires the exact trusted artifact from the recorded successful run; an older generation is not a fallback for a newer unresolved turn.

Claude's server-side prompt cache is separate from this disk history and encrypted transport. Cache expiration can make a resumed request perform more uncached work; it does not close the logical session. CI restores history, not a hidden provider KV cache. Native automatic compaction remains enabled when context needs to shrink; durable queue, goal, role, and authorization records stay outside the compacted model context.

## Failure handling

Before a paid dispatch, CI records a started generation. It records the committed generation only after the encrypted artifact is uploaded and its exact-run provenance is checked. A start without a matching committed checkpoint is unresolved, even if the previous generation is available.

The routing label can therefore exist before a successful first turn. A failed first turn with an unmatched started marker blocks subsequent dispatch; the label alone cannot recreate a session or authorize replay.

| State or failure | Handling |
| --- | --- |
| `sleeping` / `awaiting_comment` | Wait for the next eligible comment; no provider request runs while idle. |
| `pre_spawn_failed` | The command remains queued. A later wake may retry because Claude was never started. |
| `provider_outcome_unknown` or an unresolved started generation | Pause for investigation. Do not automatically replay a command that may already have run. |
| Missing, expired, invalid, or undecryptable checkpoint/history | Block recovery. Do not recreate the session or restore an older checkpoint automatically. |
| Explicitly closed logical session | Terminal in the runner; normal turn completion does not close it. |

Investigate the recorded source comment, Actions run, checkpoint marker and artifact metadata before deciding recovery. Do not paste decrypted transcripts or provider credentials into issue comments or logs. Discussion requests cannot enable repository tools, merges, deployments, hooks or MCP access in this slice.

## Live acceptance trial

Use a dedicated trial issue so that implementation instructions do not become dialogue input. Record the deployed workflow head and keep a link to each source comment and Actions run.

1. Post the initial session command with a neutral phrase to remember. Confirm the started marker precedes the `claude-session` label and first provider dispatch. Then confirm a real provider reply and committed checkpoint.
2. Record the logical session ID, native UUID, generation, comment watermark, run ID, artifact ID and ciphertext digest from the sanitized marker.
3. Let the first worker finish. Confirm `sleeping` / `awaiting_comment`; do not keep it alive with polling.
4. Post the plain recall question from an authorized writer. Confirm a different hosted run restores the previous artifact and answers with the earlier phrase without repeating it in the new question.
5. Check that the native UUID is unchanged and the next committed generation cites the new run and artifact. The matching UUID supports the evidence; memory recall from restored native history proves more than ID equality alone.
6. Add another comment while a turn is active. Confirm both source IDs complete in order and the active run is not canceled. A repeated delivered source ID must not cause another paid dispatch.

Mark the live trial accepted only when the recorded provider responses and restore evidence pass. If a run blocks, preserve that evidence and diagnose it rather than posting the same command repeatedly.

## Remaining issue #703 scope

This slice is intended to establish durable dialogue and hosted comment wake-up; that outcome remains pending the live acceptance trial. It does not give the dialogue worker repository implementation tools. Full development-session orchestration across multiple issues/PRs, awaited material CI-result wake-ups, role-specific execution capabilities, and broader crash/compaction acceptance remain separate issue #703 work. Production deployment, secret changes, and protected merges still follow `AGENTS.md`.
