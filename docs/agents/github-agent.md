# Logos as a GitHub agent

Logos can take a GitHub issue and work on its own code. The workflow is
`.github/workflows/logos.yml`; the headless runner is `issue_runner.py`.

## How it works

1. A maintainer (OWNER, MEMBER or COLLABORATOR) mentions `@logos` in an issue or
   issue comment, or adds the `alice` label. Pull request comments are ignored.
2. The runner creates a persisted invocation, marks it running, binds an `ExecutionTrace`, and then runs Logos with only its filesystem tools. They are sandboxed to the checkout
   (`tool_providers.filesystem.BASE_DIR`), so Logos edits its own code. It has no git,
   shell or network tools. Tools that need approval stop the run instead of
   being approved automatically.
3. The diff is checked: no changes under `.github/`, env files or keys, and at
   most 20 files. Then `compileall` and the runtime and frontend policy
   validators run.
4. With changes, the workflow commits to `alice/issue-<N>-<run id>`, pushes and
   opens a **draft** pull request that closes the issue. Full CI runs on it, and
   the maintainer (see `AGENTS.md`) marks the draft ready once CI is
   green, then reviews and merges it.
5. The runner persists the final trace and closes the invocation as completed or failed. Logos then comments on the issue with its summary, status and the cost of the run from `ExecutionTrace` billing (tokens and rubles; CPU energy once compute billing is merged).

## Setup

Add these repository secrets under Settings → Secrets and variables → Actions.
Never paste a token into an issue, a chat or a commit.

| Secret                  | Value                                          |
| ----------------------- | ---------------------------------------------- |
| `YANDEX_API_KEY`        | Yandex AI Studio API key for Logos             |
| `YANDEX_PROJECT_ID`     | Yandex AI Studio project (folder) ID           |
| `ALICE_APP_PRIVATE_KEY` | Full PEM private key of the Logos GitHub App   |

And this repository variable:

| Variable              | Value                                   |
| --------------------- | --------------------------------------- |
| `ALICE_APP_CLIENT_ID` | Client ID of the Logos GitHub App       |

Logos posts under its own name through the Logos GitHub App. Create it
under GitHub → Settings → Developer settings → GitHub Apps → New GitHub App:

- Name: a unique Logos name (`Logos maksimp6` for this repository). Homepage:
  the repository URL. Webhook: off.
- Repository permissions: Contents, Issues and Pull requests set to read and
  write. Nothing else.
- Where can this app be installed: only on this account.
- After creating it, copy the Client ID into `ALICE_APP_CLIENT_ID`, generate a
  private key and paste the whole `.pem` into `ALICE_APP_PRIVATE_KEY`, then
  delete the downloaded file.
- Install the App on `maksimp6/Chat` only.

Commits, pull requests and comments then appear as the App's bot (`logos-maksimp6[bot]`
for this repository): the
workflow sets the commit author to the App's bot name and its ID-based
noreply email. Unlike `GITHUB_TOKEN`, these pushes trigger CI.

When the App key is missing or App authentication fails, the workflow falls back to `ALICE_GITHUB_TOKEN`, a
fine-grained personal token of the maintainer (only `maksimp6/Chat`; Contents,
Pull requests and Issues read and write). Its posts appear under the
maintainer's name, and commits are authored as `Logos`. Delete that secret once the App works.

The workflow attempts App authentication only when both the Client ID and PEM
secret are present. It creates a short-lived installation token for each run;
do not store that generated token as the permanent secret. Before calling the
model, the workflow requires a token and checks repository access with a
read-only API request. Missing, revoked or expired credentials stop the run
before the model call. The read check does not prove a personal token has write
permissions; configure the scopes above and verify actual publication separately.

The final report runs after failures as well as success. If no valid result was
produced, it reports a failure rather than a successful empty answer. It writes
the job summary before attempting the issue comment, so diagnostics remain in
Actions even when no publishing token is configured. A failed diff guard,
model run or fast check prevents PR creation; the final report cannot commit or
push files. Its inline renderer uses isolated Python so workspace startup code
cannot run with the publishing token. PRs explicitly target `develop`.

Before diff checks and publication, an `always()` step uploads only
`result.json` as `logos-response-<run_id>-<attempt>`, with a 90-day retention
request. The result contains the full assistant-facing reply, its character
count and UTF-8 SHA-256, provider response identity and availability state.
The issue comment contains a bounded preview and an artifact link; it never
claims that `no_changes` is a review verdict or owner acceptance. A missing
artifact or missing/empty final reply fails reporting. A tool-approval pause
remains an explicit `needs_approval`, not an accepted review.

The worker checkpoints the response before persisting its invocation. A
persistence error must retain the available reply and report failure separately.
The artifact does not include the temporary credential store or raw tool trace.
Artifacts are subject to GitHub retention and deletion; archive needed evidence
before expiry. Artifact upload and a successful comment API call still do not
prove that the owner read or accepted the answer.

For an existing response, `scripts.logos_dialogue --response-id <id> --out <path>`
performs one GET and exports available assistant text without another generation
or tool execution. This requires an existing authorized provider credential;
an unavailable response remains unknown and must not be replaced with a new
generation presented as the old answer. The exported text excludes reasoning.

Optional repository variable `ALICE_AGENT_MODEL` selects the model key from
`config.py` (default `logos-runtime`).

Protect `develop` with a required pull request and green CI, so draft PRs from Logos
can never land without review.

## Limits

- One run per issue at a time (`concurrency: alice-<issue>`), 30 minutes, at most
  16 tool rounds.
- The issue text is passed to the model as task data after `AGENTS.md`. Only
  maintainers can trigger a run.
- Reports from Logos escape `@` mentions, so its own comments cannot trigger it
  again.
- The run uses a temporary database and a per-run credential encryption key.
  Nothing is settled to Treasury; the cost is only reported.
