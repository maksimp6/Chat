# Codex task: Cloud.ru provider connector

Implement GitHub issue #417 in this branch.

## Objective

Build a first-class Cloud.ru provider for Alice Pro that manages Cloud.ru as a platform, not merely as a VM host.

Do not create a VM-only integration. The design must support multiple Cloud.ru service domains and future cloud providers.

## Required design

Create a provider-neutral cloud abstraction with a Cloud.ru implementation.

Suggested structure:

```
alice_app/providers/cloud/
├── base.py
├── registry.py
├── models.py
├── policy.py
└── cloudru/
    ├── auth.py
    ├── client.py
    ├── compute.py
    ├── storage.py
    ├── network.py
    ├── databases.py
    ├── kubernetes.py
    ├── observability.py
    ├── security.py
    └── billing.py
```

Adapt paths to the actual repository architecture if an existing provider/tool abstraction should be extended instead of duplicated.

## First step

Before coding:

1. Inspect the current tool/provider architecture, Execution Trace implementation, settings/secrets handling, confirmation flow, tests and docs.
2. Reuse existing contracts instead of introducing parallel frameworks.
3. Check current Cloud.ru public API documentation before implementing concrete endpoints. Do not guess endpoint paths, authentication fields, service names or payloads.
4. Keep the implementation incremental. The PR does not need to expose every Cloud.ru service immediately.

## MVP

Implement a useful foundation covering:

- Cloud.ru auth/client foundation.
- Provider interface.
- Capability/service registry.
- Resource discovery/inventory.
- Compute list/status/start/stop/reboot where supported.
- Metrics/log query path where supported.
- Snapshot/backup entry point where supported.
- Cost/usage summary where supported by available API.
- SSH execution for commands inside VMs, clearly separated from infrastructure management.
- Agent/tool exposure compatible with the current Alice tool loop.
- Execution Trace integration.
- Tests.
- Documentation.

## Agent-facing contract

Prefer a small capability-oriented surface over hundreds of hard-coded tools.

Target direction:

```
cloud.capabilities()
cloud.resources.list(service=...)
cloud.resources.get(type=..., id=...)
cloud.compute.*
cloud.storage.*
cloud.database.*
cloud.kubernetes.*
cloud.network.*
cloud.logs.query(...)
cloud.metrics.query(...)
cloud.backup.*
cloud.costs.summary(...)
cloud.iam.*
```

Naming may differ to fit current conventions, but the abstraction must remain provider-neutral.

## Safety

- Never expose Cloud.ru credentials, access keys, secret keys, bearer tokens, SSH private keys or other secrets to the frontend, chat responses, logs, exceptions or Execution Trace.
- Redact secret values before tracing/logging.
- Read-only operations may execute normally.
- Destructive, irreversible, security-sensitive or cost-creating operations must pass the existing explicit-confirmation mechanism.
- Do not silently delete, recreate, resize or stop production resources.
- Preserve backwards compatibility.

## Architecture expectations

The Cloud.ru provider should distinguish:

1. Infrastructure API operations.
2. Cloud CLI fallback/adapter where genuinely useful.
3. SSH execution inside a machine.

Do not implement infrastructure actions by shelling into a VM when a provider API exists.

Implement capability discovery so future providers such as DigitalOcean or Proxmox can fit the same higher-level interface.

## Tests

Add focused tests for at least:

- auth/token handling with mocked transport;
- secret redaction;
- registry/capability discovery;
- provider dispatch;
- normalized resource models;
- API error mapping;
- confirmation policy for dangerous actions;
- Execution Trace visibility;
- SSH vs infrastructure-operation separation.

Avoid live Cloud.ru calls in CI. Use mocks/fixtures/contracts.

Run the repository's normal validation suite and keep CI green.

## Documentation

Document:

- architecture;
- configuration/environment variables;
- credential handling;
- supported MVP capabilities;
- how to add another Cloud.ru service module;
- how to add another cloud provider;
- confirmation/safety behavior;
- example agent calls.

Update existing docs rather than creating duplicate documentation where appropriate.

## Delivery rules

- Work only in this branch.
- Keep commits focused and understandable.
- Do not rewrite unrelated code.
- If repository reality conflicts with this prompt, follow existing architecture and explain the deviation in the PR.
- Do not merge the PR.
- At the end, update the PR description with what is implemented, remaining limitations, tests run and any follow-up issues that should be created.
- CI must be green before this PR is considered ready.

Closes #417
