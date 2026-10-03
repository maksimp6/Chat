---
name: rkn-compliance
description: Produce evidence-backed RKN compliance findings for one reviewed repository head without granting legal or release approval.
---
## Purpose
Support the six compliance roles in issue #729 with a shared evidence contract.

## Non-goals
Do not certify legal compliance, approve policy, submit or change RKN notifications,
merge, deploy, access production personal data, or implement application changes.

## Inputs
- Canonical issue, assigned role, exact reviewed commit and bounded task.
- Relevant code/configuration, declared intent and dated official legal sources.
- Sanitized deterministic test or runtime-probe artifacts, when available.

## Tools
Use only tools available to the selected profile and authorized for the task.
Read-only roles inspect code and public official sources. Test/drill/gate roles may
run isolated local checks using synthetic data. No production mutations or external
messages. A prompt restriction is not a technical sandbox for an external MCP tool.

## Procedure
1. Record stage, owner, head, scope, actual tools and stop conditions in the task handoff.
2. Read agents/compliance/README.md and select only the assigned role's work.
3. Distinguish repository declarations from observed runtime facts. A dependency,
   documentation statement or configured CI step does not prove runtime behavior.
4. Record official source URLs and UTC verification dates for legal assertions;
   recheck applicability for each release. Never infer current law from model memory.
5. Return pass, fail or unknown with a concise summary and sanitized evidence.
   Use unknown for inaccessible sources, untested scenarios or missing observations.
6. Produce the JSON report described in agents/compliance/README.md. Reports must
   identify the exact reviewed head. Do not fabricate timestamps, artifacts or traces.
7. The gate role aggregates the five specialist reports and its own report using
   scripts/rkn_agent_reports.py. Keep Inspector/linker results separate and visible.

## Approval boundaries
Legal submissions, policy approval and changes to submitted RKN information require
explicit human confirmation. Production deployment and destructive operations retain
AGENTS.md approval requirements. Agent findings never provide that approval.

## Validation
All six roles, matching commit, recent UTC checks, nonempty evidence and pass statuses
are necessary for an evidence handoff. They are not sufficient for legal compliance
or deployment. Critical findings and unknowns remain blocking regardless of prose.

## Failure behavior
Stop the affected check and report unknown or fail when evidence or capabilities are
missing. Never silently upgrade the model, weaken checks, or relabel unknown as pass.

## Output
One JSON report per role, with evidence references rather than raw personal data or
secrets. The deterministic aggregator returns a review-handoff result, never release
authorization or a fabricated ExecutionTrace. Real ExecutionTrace integration is future work.
