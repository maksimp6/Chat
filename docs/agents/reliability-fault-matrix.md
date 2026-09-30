# Reliability fault-injection matrix

Issue #594 is the adversarial companion to the 99.99 control-plane SLO in #592.

The matrix intentionally injects deterministic bad states into existing safeguards.
A scenario passes only when the expected detector observes the fault.

Current executable scenarios cover:

- stale head during readiness collection;
- branch behind master;
- missing required check;
- unresolved review thread;
- maintainer stall after the allowed Observer passes;
- Observer heartbeat from the future;
- strong-model retry without new evidence;
- duplicate exact-context read;
- cross-scope leak reported as a catastrophic reliability event;
- approval bypass;
- self-authority expansion;
- unsafe production action;
- private-key exposure into shared sanitization.

The matrix uses four independent detector families: merge readiness, Operations Observer,
the reliability SLO evaluator, and trace sanitization.

This does not replace subsystem-specific regression tests. It proves that the top-level
control plane can recognize representative injected failures and emit machine-readable
evidence for the reliability program.

Run from Python:

```python
from agent_office.fault_matrix import report_fault_matrix
print(report_fault_matrix())
```

All scenarios must report `detected=true` before this slice is considered healthy.
