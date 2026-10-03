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
| Bounded repository maintenance | Claude Lite workflow | Claude Sonnet 4.6 |
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

## Scoped work admission and handoff

Use the electrical permit-to-work system as a **process analogy** for clear
responsibility, workplace preparation, admission, supervision, and closeout.
Russian electrical-safety rules do not regulate repository agents. The relevant
role distinctions appear in [Mintrud Order No. 903n, section V](https://publication.pravo.gov.ru/Document/View/0001202012300142),
as amended by [Order No. 287n](https://publication.pravo.gov.ru/document/0001202505300025).
The electrical rules allow combinations and exceptions in particular settings;
the analogy does not require a separate human or agent for every checkpoint.

| Electrical-work function | Repository checkpoint | Boundary |
| --- | --- | --- |
| Issuing the work permit | Issue owner or Team Lead defines a bounded task, deliverable, owner, and conditions in the canonical Issue/TaskPacket. | The issue is the source of scope, not authority to bypass repository approvals. |
| Issuing permission to prepare the workplace and admit workers | Authorized coordinator verifies permissions and stage prerequisites, including an isolated branch for file changes. | Do not invent a permanent role or require a separate issuer on every routine task. |
| Admitting worker | Before execution, the dispatching owner checks the actual backend/tool profile and stage-specific evidence against the intended task. | Contract and contract-review use issue criteria; accepted-contract evidence is required from implementation onward. |
| Responsible leader and work producer | Name one implementation owner; for complex or risky work, name a coordinating lead and explicit handoffs. | The lead is conditional; a reviewer cannot silently become the implementation owner. |
| Observer and crew | Specialists work within their assigned scope; Operations Observer watches coordination signals under its own read-mostly policy. | The statutory electrical observer has a specific crew-safety purpose and is **not** Alice's Operations Observer. |

Apply these checkpoints in the canonical issue and its current task handoff.
Issues #634/#637 own TaskPacket implementation; #685/#686 own profile metadata
validation. Once those contracts land, record the same evidence there. This page
defines how humans and agents use the evidence, without adding a second state
machine or changing workflow permissions.

1. **Issue the task.** Link the canonical issue and its acceptance criteria; state
   the current stage, one primary owner, expected artifact, scope, dependencies,
   budget, approval boundary, and explicit pause conditions. A small routine
   task needs a compact record, not a new sign-off ceremony. Link accepted
   contract provenance when the stage reaches implementation or later.
2. **Prepare and admit.** For a write stage, check the current `master`/PR head
   and isolated branch. For read-only observation or review, check the relevant
   issue/PR and head when one exists. Check the role/backend, selected profile
   and skill versions, actual callable tools, inputs, and required approval.
   Record only evidence needed to reproduce the decision. Missing applicable
   branch, tool, approval, or stage-specific contract evidence blocks that stage
   until corrected.
3. **Work within scope.** The assigned owner makes the smallest reviewable
   change. Pause and update the task/handoff when scope, branch head, backend
   capability, approval, or accepted contract changes. Recheck admission before
   resuming; a previous green check or dispatch does not carry over to a changed
   head or execution profile.
4. **Transfer and close out.** Hand off the artifact, exact head, tests/CI,
   review findings, unresolved risks, and next owner. Follow the protected PR
   review and merge policy in `AGENTS.md`; verify the requested product behavior
   after merge where practical. A posted trigger alone is not proof of work, and
   a merged PR alone is not proof of the user-visible outcome.

For example, #709 required a local-launch smoke fix. Its authorized implementation
scope, PR head, and passing CI supported a protected merge. The live Yandex
chat/trace and Cloud.ru outcome remain separate verification items; do not mark
those outcomes complete merely because the PR merged. A backend dispatch that
cannot perform the required authorized GitHub operation through its available
CLI, API, or connector is paused and either given an approved capability path or
reassigned with a fresh handoff, rather than repeatedly retrying the same
blocked worker.

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


## Skill layer

Roles describe responsibility; repository skills describe repeatable procedures.
Canonical skills live under `.agents/skills/<name>/SKILL.md`.

Alice exposes a compact skill catalog and loads the full body only for skills selected
for one invocation. The selected skill name, source and content version are recorded in
Execution Trace, while the full skill prompt is not copied into the trace.

Role prompts reference reusable skills instead of restating their procedures. Global
safety, authorization and approval policy always overrides a skill.

## Compliance evidence roles

Issue #729 adds six manually selected compliance profiles: RKN Law, Data Mapping,
Policy Diff, Privacy E2E, Incident Drill and Compliance Gate. See
[the compliance agent contract](../../agents/compliance/README.md) for matching
GitHub/Codex profiles, GPT-5.5/low settings, evidence reports and deterministic
handoff validation. These specialist profiles do not grant deployment or legal
approval and are not new application runtime roles.
