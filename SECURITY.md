# Security Policy

> [!IMPORTANT]
> Report vulnerabilities **privately** through GitHub. Never open a public issue,
> discussion or pull request, and never post a commit comment about a suspected
> vulnerability, leaked credential, authentication bypass or other
> security-sensitive problem.

## Supported versions

Logos has no published releases or tags. Security fixes land on the integration
branch only.

| Line | Status | Notes |
| --- | --- | --- |
| `develop` | Maintained | Integration branch and GitHub default branch. Fixes land here. |
| `master` | Not maintained | The owner's personal branch. Promotion needs separate owner authorization. |
| Older commits and forks | Not maintained | Update to the current `develop` head before reporting. |

## Reporting a vulnerability

1. Open the repository's **Security** tab and choose **Report a vulnerability**,
   or go directly to
   [github.com/maksimp6/Chat/security/advisories/new](https://github.com/maksimp6/Chat/security/advisories/new).
   This uses GitHub private vulnerability reporting, which is enabled for this
   repository.
2. Describe the problem as listed below. Further discussion stays in that
   private advisory.
3. Keep the issue private until the repository owner (@maksimp6) has addressed it
   or agreed to disclosure.

If private reporting is not available, contact the maintainer privately through
GitHub and include enough information to reproduce the issue safely.

### What to include

- the affected component (file, module, endpoint or tool) and the `develop`
  commit SHA you tested;
- impact and preconditions, including what network access an attacker needs;
- minimal reproduction steps;
- relevant ExecutionTrace or `logs/system.jsonl` excerpts, `trace_id` /
  `invocation_id` values, with secrets removed;
- a suggested mitigation, if you know one.

Never include API keys, IAM tokens, passwords, bearer tokens, private keys,
session secrets, personal data or production credentials. If you found an
exposed credential, give its location (file, commit or URL), not its value.

## Current security posture

Read this before deploying Logos. It is the documented state described in the
[README "Current status"](README.md#current-status) and
[shared local access](docs/security/github-login.md):

- Logos runs as a shared local application **with no visitor sign-in**. The web
  interface, MCP, project board and administration APIs accept requests without
  cookies, bearer tokens or GitHub login.
- Built-in tool/agent approval, filesystem/command restriction, quota and budget
  gates are disabled. Old `LOGOS_REQUIRE_SHORT_TOKEN`, `LOGOS_UNRESTRICTED` and
  generation permission settings do not turn them back on.
- **Anyone who can reach the server can use its available APIs and tools**,
  including local and MCP tool execution.
- `LOGOS_OWNER_ID` identifies accounting/data records; it is not a password.
- Runtime threads are not a hostile-code security boundary.

For local development, set `HOST=127.0.0.1` as the
[installation guide](docs/setup/installation.md) describes.

Behavior that matches this documented posture, such as an API answering
without sign-in, is already known. Reports are most useful when they show
impact beyond it.

## Scope

In scope:

| Area | What matters | Reference |
| --- | --- | --- |
| Provider keys | Yandex AI Studio API keys and IAM tokens, Cloud.ru Foundation Models keys, stored provider credentials and their rotation. Without `LOGOS_PROVIDER_CREDENTIAL_KEY`, saved provider keys are stored unencrypted. | [Key manager](docs/security/key_manager.md), [provider key lifecycle](docs/provider-key-rotation.md) |
| Cloud.ru credentials | Cloud.ru API keys and Secret Management values. Logos keeps only a reference to the pinned version; the value must not reach logs, traces, MCP, UI or errors. | [Cloud.ru Secret Management](docs/security/cloudru-secret-management.md) |
| MCP bearer tokens | Bearer tokens and other credentials must not appear in MCP arguments, prompts, ExecutionTrace or logs. The Logos MCP transport itself has no bearer-token checks. | [MCP access and data](docs/mcp/chatgpt_apps.md#access-and-data) |
| Trace and log sanitization | Secrets are sanitized at the trace/log boundary. ExecutionTrace, ordinary logs, API responses and frontend storage must never contain secret values. | [Execution Trace and system log](docs/backend/logging.md), [key manager policy](docs/security/key_manager.md) |
| Local tool execution | `UniversalToolExecutor`, local and MCP tools, and the `GET /api/local-files` directory-listing boundary. | [Local-files directory boundary](docs/security/local-files-confinement.md) |
| Other secrets | SSH credentials, signing keys and passwords, user passwords and session secrets. | [README: configuration and security](README.md#configuration-and-security) |

Out of scope:

- the documented missing sign-in and disabled gates on their own (see above);
- vulnerabilities in external services (Yandex, Cloud.ru, GitHub) or in vendored
  upstream libraries under `static/vendor/`, `browser/emulator/vendor/` and
  `third_party/`. Report those to their maintainers as well; a report about
  how Logos uses them is in scope.

## Secret handling

Secrets must stay out of source control, issues, PRs, ExecutionTrace,
client-visible errors and normal application logs. Use environment variables or
the configured secret/credential store.

## If a secret is exposed

Revoke or rotate it first, then record the incident without copying the
secret into the repository, issues, traces or logs. Provider key rotation is
described in the [provider key lifecycle](docs/provider-key-rotation.md).

## Security documentation

- [Shared local access](docs/security/github-login.md): current access model
- [Key manager](docs/security/key_manager.md): encrypted secret storage and `key_ref`
- [Cloud.ru Secret Management](docs/security/cloudru-secret-management.md): version references for externally managed secrets
- [Local-files directory boundary](docs/security/local-files-confinement.md): `GET /api/local-files` confinement
- [Owner-authorized unlock protocol](docs/security/issue-650-owner-unlock-protocol.md): design only, no device unlock
- [Provider key lifecycle and rotation](docs/provider-key-rotation.md)
- [Execution Trace and system log](docs/backend/logging.md)
