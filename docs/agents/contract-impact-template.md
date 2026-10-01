# Contract impact map — template

Use one instance per changed rule when opening a behavioral contract PR.
Record the complete map before Team Lead review. Accepted test blobs are
immutable; amendment requires independent review.

Reuse the existing protection mechanism from #604/#607 rather than adding a
second approval gate.

## Impact map fields

| Field | Required evidence |
| --- | --- |
| **Rule** | Intended behavior and authoritative policy source (issue, contract doc, AGENTS.md section) |
| **Implementation** | Public entrypoint path; relevant production paths changed |
| **Existing tests** | Related positive and negative expectations — search for them; do not rely on memory |
| **New tests** | Required regressions; exact expected RED cases on unimplemented base; test blob |
| **Superseded expectations** | Old assertion verbatim; reason it was wrong; independent amendment approval link |
| **Documentation** | Matching prose claims or explicit OPEN limitations in contract/runbook |
| **Provenance** | Exact head SHA and accepted test blob(s) at contract acceptance |
| **Responsibility** | One primary owner; next stage; actual capability or blocker |

## Review checklist (Team Lead)

Before handoff to Infra/Backend implementer:

- [ ] All eight fields above are populated with verifiable evidence
- [ ] Every superseded expectation has an independent amendment approval link
- [ ] Accepted test blobs are recorded and frozen; no implementer may edit them
- [ ] OPEN limitations (automatic prose-semantic detection, natural-language authority) are documented rather than claimed solved
- [ ] Implementation scope is bounded: no workflow changes, no authority expansion, no second state machine beyond #604/#607

## Review checklist (Test Engineer)

- [ ] Fixtures use the ACTUAL public entrypoint (`tests/validate_skills.py`), not a mock validator
- [ ] Negative fixtures are valid except for the single targeted violation
- [ ] Each negative test asserts `returncode != 0` AND at least one stable diagnostic token from the contract token table
- [ ] No `xfail`, `skip`, or weakened assertion is added to make a RED test pass prematurely
- [ ] `test_deterministic_output_no_writes` captures snapshots before the first run

## Review checklist (Docs Engineer)

- [ ] Contract doc separates shipped behavior from OPEN design
- [ ] Diagnostic token table and stage provenance are factually current
- [ ] No unmerged change is presented as shipped master behavior
