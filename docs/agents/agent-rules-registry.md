# Agent Rules Registry — candidate v1

Contract: Issue #1159, owner request 2026-10-11. Error → evidence → proposed rule
and regression → review → admission. This PR supplies executable candidate code;
it does not activate rules in the production chat path or change merge permissions.

## Registry and boundaries

`agent_rules/registry.json` defines AR-001–AR-006, their violation states and
`proposed` lifecycle. `load_registry` rejects missing/duplicate IDs, weakened
violation states and self-activation. The controller has no activation or
policy-editing tool. Policy revisions need a protected, reviewed PR. Existing
repository protections are authoritative; Python source in the same process is
not a sandbox against malicious code. A trusted host must own code/configuration,
credentials and tool dispatch outside agent write access before rollout.

| Rule | Executable check | Violation |
| --- | --- | --- |
| AR-001 | Host observes device before each dispatch; mismatch or observer error prevents execution. | BLOCKED |
| AR-002 | Action-task closeout requires at least one dispatch; text alone cannot complete it. | FAILED |
| AR-003 | A host outcome verifier must confirm every successful action; unverified/failed outcomes prevent success closeout. | FAILED |
| AR-004 | Host resolves the actual tool destination before dispatch and compares it to task scope. | FAILED |
| AR-005 | A repeated failed method or second failed outcome creates a proposed rule record and a regression scenario linked to its runnable regression test. | REVIEW |
| AR-006 | Same tool, canonical arguments and transport after failure is refused, regardless of call ID. | BLOCKED |

A proposal is not a new approved rule or a generated domain-specific executable
regression. Its linked generic regression already tests retry refusal. The reviewer
must add the incident-specific test and evidence before approving any new rule.

## Host adapter contract

Create a frozen `TaskScope` from the authorized TaskPacket, not model arguments.
Use the same `GuardedTask` for the full action task across providers and continuations.
It serializes before/during/after checks, calls `UniversalToolExecutor`, preserves
its schema/authorization/policy/approval hooks and refuses execution after closeout.
Never expose the raw executor, controller constructor or callbacks as agent tools.
Tool routing and verification callbacks must be deterministic host-owned functions:

- `observe_device`: actual selected endpoint identity, not a user-agent string or
  model claim. Route execution to that same endpoint. This candidate cannot make
  an arbitrary external device observation and dispatch atomic.
- `observe_destination(call)`: resolve tool route (e.g. `discussions` or `issues`)
  without writing. Unavailable route identity fails closed. The controller cannot
  infer routes from arbitrary tool names or free text.
- `verify_result(result)`: independently check the task-specific receipt/read-back,
  target, resource identity and outcome. Never return true merely because the tool
  returned `success=true`; do not trust a model-supplied receipt.
- `trace`: the invocation-owned ExecutionTrace, initialized with existing context.
  Violations/checks use `add_event`. This preserves context without copying secret
  arguments or provider errors into new events. Existing executor tool-call tracing
  is separate; this wrapper does not replace or duplicate it.

`finish(claim_success=True)` fails after any violation or unverified attempt.
A non-success closeout reports FAILED. This is action-task scope; advice-only tasks
must not enter this controller. A new call ID or reordered argument keys cannot
bypass retry refusal. Changed arguments are a different method, not proof that the
cause was fixed. Admission for changed attempts still belongs to the host TaskPacket.

## Coverage, limitations and rollout

All provider identities, including ChatGPT, use identical checks **if** their tools
are routed through this host boundary. ChatGPT tools outside Alice Pro, direct CLI,
GitHub partner agents and other bypass paths are not currently intercepted. No
account-wide or universal enforcement is claimed. No global enforcement switch is
added, and the candidate is not constructed by the production application.

Failed-method history and proposals are task-instance memory, not durable storage.
The host must persist/recover trusted task history before allowing restart/resume;
recreating a task object resets history. Trace contains only salted method identifiers
and rule/status/phase/version fields, avoiding argument/error leakage. Trace-write
failure propagates and stops dispatch; a post-action trace failure cannot undo an
already executed action. Distributed locks, atomic endpoint fencing, durable incident
storage, task-wide route coverage and trusted release provenance need rollout work.

Before activation: independent review, required exact-head CI, negative adapter E2E
(wrong phone, Issues substitution, forged receipt, restart, bypass dispatch), trusted
host permission checks and explicit owner activation acceptance. Keep this PR Draft,
without merge. Never treat its tests as proof of live device control or activated rules.

Validation: `python -m pytest -q tests/test_agent_rules.py tests/test_universal_tool_platform.py`.
The tests include real ExecutionTrace events and fake-tool dispatch counters to prove
blocked actions do not execute, observer/verifier failures, proposal copy safety,
existing approval refusal and unchanged-method retry prevention.
