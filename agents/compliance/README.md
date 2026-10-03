# Compliance agents (#729)

Six manually selected GitHub/Copilot profiles live in `.github/agents/`; matching
Codex profiles live in `.codex/agents/`. They share the `rkn-compliance` skill.
They produce findings for review; they are not a production compliance service.

| Profile | Responsibility | Codex sandbox |
| --- | --- | --- |
| rkn-law | Current official law, forms, deadlines and dated citations | read-only |
| data-mapping | Personal-data flows, storage, recipients and unknown runtime facts | read-only |
| policy-diff | Data-map versus policy/notification discrepancies | read-only |
| privacy-e2e | Synthetic personal-data lifecycle tests | workspace-write |
| incident-drill | Synthetic incident timeline and draft-notification readiness | workspace-write |
| compliance-gate | Aggregate evidence and deterministic check results | workspace-write |

The three workspace-write profiles may execute isolated checks that create temporary
artifacts. They must not change application code, production data or declarations.
GitHub tool lists and Codex sandbox settings are backend-specific; neither restricts
an independently configured external MCP service. Use only authorized capabilities.

## Model and invocation

New profiles pin `gpt-5.5`, with `model_reasoning_effort = "low"` in Codex as requested.
The existing general-purpose agent defaults remain unchanged. Official documentation
checked on 2026-10-03 lists GPT-5.5 for Codex and GitHub Copilot:

- https://developers.openai.com/codex/concepts/subagents.md
- https://developers.openai.com/codex/subagents
- https://docs.github.com/en/copilot/reference/ai-models/supported-models
- https://docs.github.com/en/copilot/reference/custom-agents-configuration

Availability still depends on the account/backend. GitHub custom-agent frontmatter
has no documented reasoning-effort property; select low in the backend when exposed.
Use Codex when an explicit low-effort configuration is required. Never silently fall
back or escalate if a model is unavailable: report the capability gap.

After these files are available in the working checkout, ask Codex to delegate a
bounded task to a named profile, for example:

> Use the data-mapping agent for issue #729 at commit <full SHA>. Inspect repository
> evidence only and return the JSON report required by agents/compliance/README.md.

In GitHub Agents, manually select the matching role after the profiles land on the
repository's default branch. These profiles do not add a scheduled workflow, call a
paid model in CI or register new roles in the application's Agent Gateway/agent_office.

## Report contract

Return one JSON object per role. Required fields:

```json
{
  "role": "data-mapping",
  "head_sha": "0123456789abcdef0123456789abcdef01234567",
  "status": "unknown",
  "summary": "Runtime storage location has not been observed.",
  "checked_at": "2026-10-03T12:00:00Z",
  "evidence": [
    {
      "kind": "repository",
      "source": "deploy/cloudru/services.md at the reviewed commit",
      "checked_at": "2026-10-03T12:00:00Z"
    }
  ]
}
```

Statuses are `pass`, `fail`, `unknown`. Any failed check makes the role fail; any
missing required observation makes it unknown even if other checks pass. Evidence
kinds are `official-source`, `repository`, `test-artifact`, `runtime-probe`. Use
sanitized artifact references, not raw personal data, secrets or signed URLs.
Official legal sources need provision/effective-date details in the summary/artifact.
Test artifacts need exact commands and results. `checked_at` is a UTC observation
or verification time, not the source publication date. Never refresh timestamps
without performing the check again.

The gate collects the five specialist reports and adds its own aggregation report.
An operator saves the six reports outside tracked source, then runs:

```bash
python scripts/rkn_agent_reports.py --head "$(git rev-parse HEAD)" /tmp/rkn-reports/*.json
```

The CLI rejects missing/duplicate roles, malformed reports, missing evidence,
non-pass statuses, a different commit, future dates and checks older than 24 hours.
Exit 0 means the evidence package is structurally ready for review. It **does not**
authenticate referenced artifacts, verify their contents, determine current law,
certify compliance or authorize release. `release_authorized` is always false.
A fresh report may still cite incomplete or wrong evidence: reviewers and the
future deterministic linker must verify it. The 24-hour bound is an evidence
freshness policy, not a legal deadline.

## Relationship to PR #732

PR #732 owns the repository Inspector, actual-state schema, declared intent and
compliance CI. This branch is independent and does not duplicate those files.
When that PR is available, use its manifest/linker output as additional evidence;
profile output cannot overwrite Inspector unknown/fail results. A dependency in
requirements, a documentation assertion or a configured CI command does not prove
password handling, redaction or a successful runtime backup/restore.

Runtime TLS/domain/retention probes, production release blocking, generated legal
documents, automatic execution and real ExecutionTrace integration remain separate
work. Do not claim those capabilities from the existence of these profiles. No RKN
submission or other legally significant action is performed automatically.

## Work handoff

- Canonical task: https://github.com/maksimp6/Chat/issues/729; companion PR: #732.
- Scope/contract: six roles and evidence-backed findings in #729, plus the user's
  request for agent support with current models and low effort. This slice implements
  profiles and deterministic report validation, not the full issue's Definition of Done.
- Owner: current Codex implementer, acting as Backend Engineer for the validator and
  Docs Engineer for profiles. One implementation owner; no agents dispatched.
- Branch: `codex/729-compliance-agents`, based on master
  `af3b1c924f6fd96be01feaabe96fea8fb795c223`.
- Tools: local Git/Python tests and GitHub publication tools; no model API or production
  probes are assumed available. Profile load/test is not evidence of a live model run.
- Stage: implementation → deterministic verification → draft PR; exact published head
  and validation results belong in the PR handoff.
- Approval: repository edits/draft PR are within the requested task. Existing human
  approval boundaries for legal actions, deployment and destructive changes remain.
- Stop: changed scope/head, unavailable tools/model, missing evidence, real-data access
  or a need to change production/security settings. Record the gap before continuing.
