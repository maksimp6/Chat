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
   the maintainer (Claude, see `AGENTS.md`) marks the draft ready once CI is
   green, then reviews and merges it.
5. The runner persists the final trace and closes the invocation as completed or failed. Logos then comments on the issue with its summary, status and the cost of the run from `ExecutionTrace` billing (tokens and rubles; CPU energy once compute billing is merged).

## Setup

Add these repository secrets under Settings → Secrets and variables → Actions.
Never paste a token into an issue, a chat or a commit.

| Secret                  | Value                                          |
| ----------------------- | ---------------------------------------------- |
| `YANDEX_API_KEY`        | Yandex AI Studio API key for Logos             |
| `YANDEX_PROJECT_ID`     | Yandex AI Studio project (folder) ID           |
| `ALICE_APP_PRIVATE_KEY` | Private key of the "Alice Pro" GitHub App      |

And this repository variable:

| Variable              | Value                                   |
| --------------------- | --------------------------------------- |
| `ALICE_APP_CLIENT_ID` | Client ID of the "Alice Pro" GitHub App |

Logos posts under its own name through the "Alice Pro" GitHub App. Create it
under GitHub → Settings → Developer settings → GitHub Apps → New GitHub App:

- Name: `Alice Pro`. Homepage: the repository URL. Webhook: off.
- Repository permissions: Contents, Issues and Pull requests set to read and
  write. Nothing else.
- Where can this app be installed: only on this account.
- After creating it, copy the Client ID into `ALICE_APP_CLIENT_ID`, generate a
  private key and paste the whole `.pem` into `ALICE_APP_PRIVATE_KEY`, then
  delete the downloaded file.
- Install the App on `maksimp6/Chat` only.

Commits, pull requests and comments then appear as `alice-pro[bot]`: the
workflow sets the commit author to the App's bot name and its ID-based
noreply email. Unlike `GITHUB_TOKEN`, these pushes trigger CI.

Until the App exists, the workflow falls back to `ALICE_GITHUB_TOKEN`, a
fine-grained personal token of the maintainer (only `maksimp6/Chat`; Contents,
Pull requests and Issues read and write). Its posts appear under the
maintainer's name, and commits are authored as `Alice Pro`. Delete that secret once the App works.

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
