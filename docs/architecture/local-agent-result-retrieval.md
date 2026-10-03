# Local-agent result retrieval

This is a prerequisite repair for the native laptop integration discussed in
#646 and #647, not a working laptop launcher or a browser backend.

## Implemented boundary

`UniversalToolExecutor._execute_remote` already enqueues authorized calls and
imports `local_agent_gateway.wait_for_local_tool_job`. That function was missing.
The gateway now delegates result retrieval to the dependency-free
`local_agents.results.wait_for_job_result` helper, using the configured `get_conn`.
No public enqueue/result-read endpoint or new tool permission is introduced.

`execute_with_trace` attaches a live `execution_trace` object to call metadata.
The queue excludes that process-local object rather than serializing its repr or
contents. The existing `trace_id`, `invocation_id`, and JSON request metadata stay
with the job. The result's correlation fields come from the stored job, not from
agent-supplied metadata.

The waiter uses a monotonic deadline, closes every connection before a 50 ms
polling pause, and never modifies, claims, retries or cancels a job. The default
wait is 30 seconds; accepted timeouts are finite numbers from 0 through 300
seconds. Zero performs one read. Database-driver connection/query timeouts are
configured separately; the polling deadline cannot interrupt a blocked driver.

Completed tool envelopes and plain JSON payloads are normalized to the universal
result shape. Failed jobs, failed tools, malformed envelopes, invalid JSON,
non-finite numbers, results above 1,048,576 characters and JSON nesting deeper
than 64 levels fail closed. Error codes are stable; remote exception text and
remote metadata are not echoed. Tool output data still requires each tool's
normal content/redaction policy. This is not a general-purpose secret scrubber.

A timeout returns `None` to the existing executor, which reports a timeout.
It does **not** prove that the device did not execute the job. The current queue's
lease/retry behavior is unchanged and does not establish exactly-once execution.
Do not automatically resubmit non-idempotent actions after an uncertain outcome.

## Validation

```bash
python -m pytest -q tests/test_local_agent_results.py tests/test_local_agent_gateway.py
bash scripts/format.sh check
```

The standalone result tests use real temporary SQLite storage. Added gateway
regressions exercise the real executor, queue, authenticated Flask poll/result
routes, and denial-before-enqueue behavior. Only the trace recorder and registry
lookup are test doubles; the gateway import and execution functions are not
replaced. The simulated device's response is delivered during the polling pause.
This is an integration regression, not a real-device acceptance test.

## Remaining laptop acceptance

A native Linux launcher, explicit pairing/stop controls, an approved real-browser
backend, and an authenticated ChatGPT-to-Alice tool connection remain separate
work. Browser actions must preserve the existing executor/owner/approval boundary.
Do not reinterpret emulator-only `browser_local` as access to a personal profile.
Acceptance still requires a real page observation, a harmless approved action,
and its correlated result/trace through the intended connection.
