# Logos | Review Docket

Use the [Logos court procedure](../docs/agents/logos-court-procedure.md) as the
review record's guide.

## Case file

Describe the concrete problem and resulting behavior.

## Question before the court

State the change's purpose and scope.

## Record and authorities

Link the owning issue, exact source material and any overlapping PR.

## Examination and findings

- Source/head SHA:
- Diff review and document/data checks:
- Separately authorized runner checks and run/job links:
- NOT_RUN items and remaining limitations:

Code tests require a separate owner request and run only inside GitHub Actions
runner (AGENTS.md). Do not mark an unrun check as passing.

## Operational effects

Describe applicable effects on journal compatibility, backups, credentials,
ExecutionTrace and billing. Keep secrets out of the PR and evidence.

For deployment work, link process revision/PID and the requested visible outcome.
HTTP 200 or a successful job alone does not establish user acceptance.

## Requested disposition

Follow the owner's route and AGENTS.md. Stage acceptance ("OK") does not authorize
merge or promotion to master.
