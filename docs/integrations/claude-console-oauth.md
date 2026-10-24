# Claude Console OAuth for Logos

Task: [#1586](https://github.com/maksimp6/Chat/issues/1586). This optional provider
uses the Anthropic Messages API through an official Claude Console OAuth login.
It does not use a Claude.ai subscription or collect Claude Code session files.
The default Logos/Yandex provider and repository execution-role policy are unchanged.
The Ultra catalog validates coverage of Yandex models only. Adding this foreign
provider preserves all 14 existing Yandex profiles; it does not attach Yandex
wire parameters or a Yandex Ultra profile to Claude.

## Connect on the machine running Logos

1. Install the official `ant` CLI from
   [Anthropic's installation instructions](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart).
   The documented Linux release at implementation time is 2.3.0. Verify the
   publisher and release before installing; this integration does not install binaries.
2. As the same OS user that runs Logos, execute:

   ```sh
   ant auth login --profile logos --no-browser
   ```

   Complete Anthropic's own browser flow and select the Console workspace.
   For an interactive local browser, omit `--no-browser`. Never paste access or
   refresh tokens into chat, issues, commits, or terminal commands. For another
   profile, set `LOGOS_CLAUDE_OAUTH_PROFILE` to its name in the Logos environment.
3. Create a new conversation with model `claude-opus-5-5`. The static/degraded
   catalog and the successful Yandex discovery catalog both retain this option.
   Creating the local conversation does not perform login or a model call, and
   presence in the catalog does not mean the account is connected.

The adapter privately calls `ant --profile logos profile get` to check the
first-party `user_oauth` profile, then
`ant --profile logos auth print-credentials --access-token` to obtain the current
access token. **Do not run the latter command interactively to troubleshoot**:
its stdout is a secret. The CLI owns token storage and refresh. The adapter
does not read refresh tokens, change the active profile, or fall back to API keys.
CLI warnings (including refresh failure) stop the request. Login authorizes
Claude Console API usage with its own billing; a Claude Pro/Max subscription is
not a Console API credit balance.

This login is intended for the owner's local development/scripting machine.
For unattended servers/CI, Anthropic recommends
[Workload Identity Federation](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication).
This slice does not configure WIF, CI secrets, or hosted multi-user OAuth sessions.

## Runtime contract

- `LogosClient` routes explicit `claude-*` selections to this adapter; unknown
  Claude models fail locally. Other selections retain the existing Yandex path.
- Initial context comes from the local conversation's text history; system
  instructions are sent separately. Text-only input and local function tools are
  supported. Tool output IDs must match the pending batch in the same client,
  model and conversation. Within one turn, the complete original assistant
  content is retained for Messages API continuations.
- Local functions execute through Logos's existing `UniversalToolExecutor` and
  approval loop. Read-only execution policies still filter tools. No new executor
  or provider-named agent profile is introduced.
- Requests use the fixed HTTPS Messages endpoint, explicit OAuth beta/version
  headers, no redirects, 10-second connect/90-second read timeouts, a 90-second
  between-chunk deadline, and 2 MiB request/response bounds. There is no automatic
  retry, fallback, or duplicate generation after a timeout. A blocked read may
  finish at its read timeout after the deadline; it cannot stream indefinitely.
- `max_output_tokens` maps to `max_tokens` (default 8192, configurable to 128000).
  One explicit sampling setting (`temperature` or `top_p`) is forwarded. Streaming,
  hosted tools, Yandex vector stores, stored-response IDs and unsupported reasoning
  profiles fail explicitly. Synchronous transport does not create background jobs.
- Response IDs, statuses, usage, request/step correlations and provider selection
  are recorded in ExecutionTrace. A truncated response remains `incomplete`.
  Cache usage stays in the receipt. No current Anthropic prices have been
  configured: its AI cost is `unknown`, currency USD, with `total_cost: null`.
  Local compute costs remain separate; they do not establish the model's price.
  The chat API returns `cost: null` when Claude AI cost is unknown.
- Claude conversations are stored locally. For provider changes, use a new
  conversation: a local-only Claude UUID is not a remote Yandex conversation ID.
  Automatic cross-provider conversation migration is outside this slice.

## Activation and verification boundary

`agent_runtime/logos_launch_hold.py` is enforced before Claude credentials,
network access, or tool discovery. The existing court hold under #1413 has not
been removed. Adding the provider does not authorize model execution or merge.
The native issue runner accepts the explicit model without seeding Yandex or
creating a Yandex conversation; its production hold also remains in place.

`tests/test_claude_oauth_provider.py` exercises synthetic OAuth CLI output, a fake
Messages transport, real Logos tool-loop/approval execution, HTTP conversation/chat
routes, invocation correlation, cost handling and failure controls. Tests that
exercise transport replace the court guard only within those offline fixtures;
separate negative controls exercise the unchanged production guard.

Real OAuth authorization, deployed UI/device behavior and a real model reply are
**NOT_RUN**. They require the target machine's official CLI, owner account login,
the separately authorized return from the court hold, and an approved API budget.
No paid model request was made during implementation.

## Primary sources

- [Console OAuth and CLI authentication](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication)
- [Official CLI source](https://github.com/anthropics/anthropic-cli), especially
  `pkg/cmd/cmd_auth.go` (profile credentials, refresh and OAuth beta header).
- [Anthropic credential-use distinction](https://code.claude.com/docs/en/legal-and-compliance)

Sources checked on 24 October 2026; model availability still depends on the
authorized workspace and must be verified with the provider before activation.
