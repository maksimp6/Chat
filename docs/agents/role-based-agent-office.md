# Role-based GitHub agent office

Alice Pro treats AI providers as execution backends and repository agents as roles.

## Layers

1. **Role** — the job to be done: backend, frontend, Android, testing, infrastructure,
   security review, documentation, release management, or coordination.
2. **Agent runtime** — GitHub Copilot custom agent, GitHub Partner Agent, or the
   repository Claude Lite workflow.
3. **Model** — the smallest model that is reliable for the task.

This keeps prompts, permissions and expertise stable even when model vendors or model
versions change.

## Default routing

| Work | Primary role/backend | Default model |
| --- | --- | --- |
| Triage and delegation | Team Lead custom agent | GPT-5.4 mini |
| Backend/API/runtime/database | Backend Engineer | GPT-5.4 mini |
| Web UI/static JS | Frontend Engineer | GPT-5.4 mini |
| Android | Android Engineer | GPT-5.4 mini |
| Tests and CI diagnosis | Test Engineer | GPT-5.4 mini |
| CI/CD/Cloud.ru/runners | Infra Engineer | GPT-5.4 mini |
| Security review | Security Reviewer | GPT-5.4 mini |
| Documentation | Docs Engineer | GPT-5.4 nano |
| Merge/release preparation | Release Manager | GPT-5.4 mini |
| Cross-agent non-convergence observation | Operations Observer | GPT-5.4 mini |
| Process-gap diagnosis and correction | Process Governor | GPT-5.4 mini |
| Architecture-heavy or ambiguous work | Anthropic Claude Partner Agent | Claude Sonnet 4.6 |
| Cheap bounded maintenance | Claude Lite workflow | Claude Haiku 4.5 |
| Focused Codex session | OpenAI Codex Partner Agent | GPT-5.4 nano |

## Dispatch rules

Prefer a repository role from the GitHub Agents UI for normal development. Team Lead
can delegate to other custom agents with the custom-agent tool.

Use provider agents as escalation/backends, not as permanent job titles:

- assign the Anthropic Claude Partner Agent for architecture-heavy or multi-domain work;
- use the OpenAI Codex Partner Agent for focused code/test tasks;
- use `@claude-lite` for short bounded repository maintenance;
- keep native Copilot automatic PR review enabled, but do not retrigger it manually.

One issue has one primary owner. Split an issue only when independent deliverables can
be reviewed and merged separately.

## Supervision and process improvement

Operations Observer and Process Governor sit above normal task execution; they are not
extra implementers in every issue.

Operations Observer is read-mostly and watches for the deterministic non-convergence
signals defined in `process-observation-and-governance.md`. It emits compact evidence
instead of deciding which disputing specialist is correct.

Process Governor consumes repeated or structural escalations and may propose changes to
agent role instructions, skills, development policy/docs, or non-privileged workflow
logic through an ordinary protected PR. It does not take ownership of the disputed
production code and cannot approve or merge its own governance changes.

Escalate to the owner only when an existing approval boundary is crossed, rather than
turning every agent disagreement into an owner interruption.

## Safety and cost

Role profiles restrict tools where useful. Security Reviewer is read-mostly. Team Lead
does not edit code. Production deployment, destructive database changes, secrets,
CODEOWNERS and branch protection still require the owner's explicit approval.

Use the lightest configured model by default. Escalation must have a concrete reason,
not merely a preference for a larger model.
