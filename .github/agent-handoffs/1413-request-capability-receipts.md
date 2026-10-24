# #1413 D10 / D5 — actual request receipts

Contract: owner's 2026-10-23 court-remediation instruction; existing
[D5/D10 remand](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6366889282)
and [implementation admission](https://github.com/maksimp6/Chat/issues/1413#issuecomment-6368155981).
Primary owner: root/native_hold, Backend/Infra Engineer; coordinator root.
Independent reviewer: root/native_hold/hold_review (read-only).
Develop base: `2e1f1bcacfe6f4762b32c3e2e5b1114448199cc3`.
Branch: `fix/1413-request-capability-receipts`.

## Implementation boundary

Immediately before the real `session.post(json=payload)`, after payload shaping
and logging, record `api_request_manifest` in the existing ExecutionTrace. The
event contains method, actual step/correlation and a safe manifest built from the
assembled payload. It does not fingerprint configuration or the sanitized trace.

`python-json-sorted-utf8-compact-v1` means `json.dumps` with sorted keys,
`ensure_ascii=False`, compact separators and `allow_nan=False`, encoded as UTF-8.
It requires supported JSON objects with string keys and valid Unicode. The hash
describes canonical semantic JSON, not Requests' exact wire serialization.
`wire_bytes_verified` is false; provider-effective settings remain UNKNOWN.

Every tracked option is explicitly present or absent in the actual payload;
absence never becomes a known provider default. Model/settings use bounded
structural or enum checks before raw scalar export; arbitrary content is hashed.
Input, instructions, tools, prompt/text/metadata and conversation/cache/previous
response linkage contain hashes/counts, not their raw content. Tool schemas,
authorization and headers are not copied into the event.

For D5's protected local history writer, optional trusted parameter
`on_provider_input(snapshot)` receives:

```
schema_version, capture_stage, trace_id, step, correlation_id, manifest, data
```

`data` is a deep copy of only the fields actually present among `input`,
`instructions`, `conversation`, `previous_response_id`. It can contain sensitive
tool messages and is exclusively for the configured protected original writer;
it is never an event or public export. Tools definitions and headers are excluded.
The callback is validated before credentials/side effects; its exception prevents
the POST and continuation. It runs before HTTP error handling and API-attempt
registration, so a local writer timeout is not reported as a provider failure or
generation. The agreed success return is None. With no callback,
the request flow is unchanged. Mutation of the callback copy cannot modify the
outgoing request or exported manifest. The callback itself is not a wire field.

No full provider-conversation replay, accepted token counter or provider delivery
is claimed by this layer. D5's writer and admission remain a separate linked
change; the receipt binds any later accepted counter to the actual payload hash.
Finalized-event export belongs to root's existing delivery change.

## Verification and handoff

- 69 focused receipt/builder/logging/continuation tests passed offline.
- Staged code-rule checks: 109 passed, including the new module's strict typing.
- Changed-line coverage: 99/99, 100%; request_receipt module has 100% line/branch
  coverage in the focused suite.
- Process-only negative control fingerprinted sanitized trace data instead; the
  actual fake-transport boundary test failed on the wrong hash/byte count.
  Production code was unchanged and no provider was called by this control.
- Initial/continuation input, full long Unicode, callback failure/mutation,
  omitted options, tool cleaning, unknown linkage, sensitive fields and a large
  JSON integer are covered. Canonical payload capture is not proof of delivery.
- Independent review found a large-int float-conversion failure; it was fixed
  without changing request settings or weakening reliability/type baselines.
  A local-writer timeout misclassified as a provider failure was also fixed;
  the reviewer independently reran 38 focused tests after the corrections.

Authorized tools: local isolated checkout, deterministic tests, ordinary PR CI and
GitHub publication coordinated by root. Model/provider budget is zero. No model,
live server/device, credentials/permissions, dispatch, merge or deployment was
performed. Stop on concurrent HEAD change or a need for any such protected action.
Exact published HEAD, CI and owner acceptance remain coordinator evidence.
