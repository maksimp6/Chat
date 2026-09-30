# Rolling reliability evidence

Issue #598 turns real control-plane events into the rolling evidence window consumed by
the 99.99% evaluator.

The first slice establishes three rules before source adapters are wired:

1. **Stable decision ids are deduplicated.** Re-reading the same decision never inflates
   the sample size.
2. **Conflicting duplicates fail closed.** Two different facts with the same stable id
   and timestamp become `unknown` with `conflicting_duplicate` provenance.
3. **Only the requested time window counts.** Future events and events older than the
   rolling window are excluded.

The default window is 30 days. The SLO evaluator still requires at least 10,000 unique
measured decisions before four-nines may be reported as proven.

This slice deliberately accepts normalized event dictionaries rather than scraping
GitHub or Execution Trace directly. Follow-up source adapters will map merge-readiness,
Observer, retrieval/cache, approval and runtime-scope telemetry into this contract.
