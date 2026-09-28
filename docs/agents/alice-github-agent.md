# Alice as a GitHub agent

Alice Pro can take a GitHub issue and work on her own code. The workflow is
`.github/workflows/alice.yml`; the headless runner is `agents/github_runner.py`.

## How it works

1. A maintainer (OWNER, MEMBER or COLLABORATOR) mentions `@alice` in an issue or
   issue comment, or adds the `alice` label. Pull request comments are ignored.
2. The runner creates a persisted invocation, marks it running, binds an `ExecutionTrace`, and then runs Alice with only her filesystem tools. They are sandboxed to the checkout
   (`filesystem_mcp_tools.BASE_DIR`), so Alice edits her own code. She has no git,
   shell or network tools. Tools that need approval stop the run instead of
   being approved automatically.
3. The diff is checked: no changes under `.github/`, env files or keys, and at
   most 20 files. Then `compileall` and the runtime and frontend policy
   validators run.
4. With changes, the workflow commits to `alice/issue-<N>-<run id>`, pushes and
   opens a **draft** pull request that closes the issue. Full CI runs on it, and
   a maintainer reviews and merges.
5. The runner persists the final trace and closes the invocation as completed or failed. Alice then comments on the issue with her summary, status and the cost of the run from `ExecutionTrace` billing (tokens and rubles; CPU energy once compute billing is merged).

## Setup

Add these repository secrets under Settings → Secrets and variables → Actions.
Never paste a token into an issue, a chat or a commit.

| Secret               | Value                                                    |
| -------------------- | -------------------------------------------------------- |
| `YANDEX_API_KEY`     | Yandex AI Studio API key for Alice                       |
| `YANDEX_PROJECT_ID`  | Yandex AI Studio project (folder) ID                     |
| `ALICE_GITHUB_TOKEN` | The maintainer's personal fine-grained token (see below) |

Create `ALICE_GITHUB_TOKEN` under GitHub → Settings → Developer settings →
Fine-grained tokens:

- Repository access: only `maksimp6/Chat`.
- Permissions: Contents, Pull requests and Issues set to read and write.
- A short expiry, rotated regularly.

Pull requests and comments then appear under the maintainer's account. Unlike
`GITHUB_TOKEN`, they trigger CI.

Optional repository variable `ALICE_AGENT_MODEL` selects the model key from
`config.py` (default `aliceai-llm`).

Protect `master` with a required pull request and green CI, so Alice's draft PRs
can never land without review.

## Limits

- One run per issue at a time (`concurrency: alice-<issue>`), 30 minutes, at most
  16 tool rounds.
- The issue text is passed to the model as task data after `AGENTS.md`. Only
  maintainers can trigger a run.
- Alice's reports escape `@` mentions, so a comment posted with the maintainer's
  token cannot trigger her again.
- The run uses a temporary database and a per-run credential encryption key.
  Nothing is settled to Treasury; the cost is only reported.
