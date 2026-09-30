# Agent control-plane reliability SLO

Issue #592 defines the measurable meaning of the Alice Pro “99.99% reliable” target.

## What the number means

The target is **99.99% valid control-plane decisions** over a real evidence window.
It does not claim that an LLM is correct 99.99% of the time.

A four-nines claim requires at least **10,000 measured decisions**. Below that floor the
auditor may report the controls as healthy, but it must not claim the SLO is proven.

Countable decisions include task dispatch, retrieval preparation, readiness decisions,
maintainer outcomes, Observer escalation, approval-boundary decisions and merge
decisions.

Unknown evidence is not counted as success.

## Catastrophic error budget

The catastrophic error budget is zero. Any observed instance blocks a reliability claim:

- stale-head or behind-master merge;
- missing/failing/pending required checks accepted as ready;
- unresolved review thread accepted as ready;
- required owner approval bypass;
- cross-repository or cross-runtime leakage;
- secret/private-key exposure in trace/memory/user telemetry;
- agent self-expansion of authority;
- destructive or production action without required approval.

## Convergence and cost signals

The auditor also reports:

- stale/missing Observer heartbeat;
- maintainer handoff without merge/BLOCKED/DEFERRED after two Observer passes;
- strong-model retries without new evidence;
- duplicate exact-context reads despite reusable cache/memory.

Observer or maintainer convergence failures block the SLO claim. Cost-waste signals are
reported separately because wasting tokens is bad engineering, not equivalent to a
security/merge catastrophe.

## Evidence contract

A decision marked valid must have authoritative provenance. GitHub, Execution Trace,
agent-memory records with source refs, CI checks and deterministic runtime telemetry are
acceptable sources.

A decision without provenance fails closed.

## CLI

```bash
python -m agent_office.reliability reliability-snapshot.json --pretty
```

Exit code is zero only when the target is met and no blocking finding exists.

The first slice evaluates an explicit JSON snapshot. Follow-up wiring will build that
snapshot from Observer, merge-readiness, retrieval/cache telemetry and approval events,
then aggregate a rolling 30-day window.
