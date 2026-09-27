# Copilot instructions

Follow [`AGENTS.md`](../AGENTS.md): repository workflow, architecture rules, and
validation commands apply to Copilot as to every other agent. This document
expands with guidance specific to the @alice GitHub agent and shared agent
dispatch patterns.

## Working with issues

- Work on a branch and open a pull request; never push to `master`.
- One issue, one focused pull request with deterministic regression tests.
- Before finishing, run:
  - `python -m compileall -q .` (Python syntax check)
  - `python tests/validate_runtime_modules.py` (runtime module validation)
  - `pytest -q` (all tests, with coverage target ≥100% for new code)
  - `bash scripts/format.sh check` (code formatting)
  - `zizmor --offline .github/workflows/` (GitHub Actions security check)
- Never commit secrets, API keys, or add CDN-hosted UI dependencies without an issue.

## The @alice agent architecture

@alice is a **headless GitHub agent** that works on her own code through filesystem
tools only. Understand these five core concepts:

### 1. Headless runner pattern (`alice_agent_runner.py`)

When an issue mentions `@alice` or has label `alice`, the workflow spawns
`alice_agent_runner.py --issue N --title-file ... --body-file ... --out result.json`.

The runner:
- Seeds a provider credential (YANDEX_API_KEY + YANDEX_PROJECT_ID) into a temp database.
- Creates a conversation and invocation with ExecutionTrace.
- Calls `AliceClient.ask_with_mcp(issue_text, max_rounds=16)` with **filesystem tools only**.
- Returns JSON: `{ status, changed_files, summary, trace_id, billing }`.
- **Never auto-approves**: if a tool call needs approval, the runner stops with `status: needs_approval`.

**Key insight**: Unlike CLI agents, the headless runner has no shell/git/network/exec
tools. Alice can only edit files (write_file, apply_patch, delete_file). This is
**intentional and enforced** — it prevents accidental commits or secret leaks.

### 2. GitHub Actions workflow (`alice.yml`)

Triggers on `@alice` mention or `alice` label (issue_comment or issues events).

**Trust gate**: runs only if `author_association` is OWNER/MEMBER/COLLABORATOR.
This prevents untrusted users from triggering Alice.

**Diff guard**: rejects changes that:
- Touch `.github/` (Alice cannot modify her own workflow)
- Add `.env`, `.pem`, `.key`, `.jks` files (no secrets)
- Exceed 20 file changes (prevents runaway edits)

**Maintainer token**: uses `ALICE_GITHUB_TOKEN` (fine-grained personal token,
contents + pull requests write on this repo only) to open the PR so CI runs
automatically. Without it, a machine-token PR sits in draft until someone
manually triggers CI.

### 3. ExecutionTrace billing

All API calls to YANDEX_API_KEY are recorded in the trace. The runner extracts:
- `total_cost` (in RUB)
- `tokens` (input/output count)
- `trace_id` (for audit)

The workflow posts this to the issue so maintainers see the cost before merging.

### 4. Filesystem sandbox

`filesystem_mcp_tools.py` restricts all file ops to `BASE_DIR` = checkout root.
This means:
- Alice edits only files in the repo (no `/etc/passwd`, no sibling dirs).
- No `cd`, `rm -rf`, `grep`, `find`, or shell expansion.
- Each file read/write is logged in the trace.

### 5. Approval-gated tools

If Alice needs a tool that requires approval (e.g., network access for a runtime
call), `AliceClient.ask_with_mcp()` pauses the loop and returns a pending tool
call. The runner detects this and stops with `needs_approval` instead of
auto-approving. A human must review and manually run the approval.

## Making changes to agent systems

### Adding a tool category or modifying filesystem tools

1. Changes to `alice_agent_runner.py`:
   - Adjust `params={"active_tool_categories": ["filesystem"]}` to add categories.
   - Test with `tests/test_alice_agent_runner.py` (mock AliceClient).
   - Run workflow tests: `tests/test_alice_workflow.py`.

2. Changes to `filesystem_mcp_tools.py`:
   - Verify `BASE_DIR` enforcement in filesystem functions.
   - Add regression tests to `tests/` (e.g., `test_filesystem_sandbox.py`).
   - Ensure no tool is sensitive to file ordering (deterministic).

### Extending the agent dispatch system

The `.github/ISSUE_TEMPLATE/agent_task.md` template describes shared structure:
- **Goal**: what the task accomplishes.
- **Acceptance criteria**: how to verify success.
- **Files/areas**: scoped to this PR (prevents scope creep).
- **Checks to run**: backend tests, Android lint, etc.
- **Out of scope**: what the agent must skip.
- **Dispatch**: mention `@claude`, `@codex`, `@copilot`, or `@alice`.

To add a new agent (e.g., `@eve`):
1. Create `.github/workflows/eve.yml` (modelled on `alice.yml` or `claude.yml`).
2. Update `AGENTS.md` with `@eve` description and when to use it.
3. Document setup in `docs/agents/eve-github-agent.md`.
4. Add workflow tests to `tests/test_eve_workflow.py` (SHA-pinned, trust gate, diff guard).

### Making changes to the workflow (`alice.yml`)

**Never directly edit the workflow syntax** without testing:

1. Run `zizmor --offline .github/workflows/alice.yml` to check for security issues
   (unused variables, unsafe shell expansion, credential injection).
2. Update `tests/test_alice_workflow.py` to verify the change (check that the
   condition still works, secrets are scoped, actions are pinned).
3. Test the runner step locally:
   ```bash
   python -m pytest tests/test_alice_agent_runner.py -xvs
   ```
4. For major changes, open a draft PR and let the workflow run on a real issue.

## Key constraints and why they matter

| Constraint | Reason |
|---|---|
| Filesystem tools only | Prevents accidental commits, pushes, or secret exposure |
| No auto-approval | Approval-gated tools require human review before proceeding |
| Diff guard (max 20 files, no `.github/`, no secrets) | Prevents agent self-modification loops and runaway changes |
| SHA-pinned actions | Protects against supply-chain compromise (action code changes) |
| `persist-credentials: false` | Checkout does not cache GitHub token in `.git/config` |
| `author_association` gate | Only repo members can trigger (prevents abuse) |
| ALICE_GITHUB_TOKEN scoping | Token is fine-grained (contents + PRs only) and short-lived |

## Debugging a failed run

When Alice opens a PR but CI fails:

1. **Read the workflow run log** (Actions → alice-* → the run).
2. **Look for the status**:
   - `needs_approval`: Alice asked for a blocked tool; check `result.json` for pending tool.
   - `failed`: runner crashed; read stderr for the exact error.
   - `changed` but CI red: Alice made a code change that breaks tests.
3. **Check `result.json` in the run artifacts**:
   - `billing` section shows API usage (tokens, cost, trace_id).
   - `summary` field is the model's plain-English output.
   - `changed_files` lists files Alice touched.
4. **If CI fails on Alice's change**:
   - Verify the change is sound (review the diff).
   - If it's a real bug in Alice's code, the workflow commits it to `alice/issue-<N>-<RUN_ID>`
     and opens a draft PR; you can fix it there or close and re-run @alice.

## Testing

### Unit tests for the runner (`tests/test_alice_agent_runner.py`)

- Mock `AliceClient` to simulate different responses (changed, no_changes, needs_approval, error).
- Verify `changed_files`, `summary`, and `billing` are present in output.
- Verify secrets (YANDEX_API_KEY) are **never** in output or logs.
- Test CLI interface: `--issue`, `--title-file`, `--body-file`, `--out`.

### Workflow tests (`tests/test_alice_workflow.py`)

- Verify actions SHA-pinned (no `@main`, all uses: sha style).
- Verify `persist-credentials: false` in checkout.
- Verify `if:` condition gates on `@alice` mention and author_association.
- Verify diff guard rejects `.github/`, `.env`, `.pem` files, >20 file changes.
- Verify issue text passed only via `env:` (not shell interpolation).
- Verify secrets are per-step (not exposed in runner environment).

### Local smoke test

```bash
# Create a temp issue file
echo "Add docstring to compute_resources.py" > /tmp/issue_body.txt
python alice_agent_runner.py \
  --issue 999 \
  --title-file /tmp/issue_body.txt \
  --body-file /tmp/issue_body.txt \
  --out /tmp/result.json

# Check output
cat /tmp/result.json | python -m json.tool
```

## Setup requirements (for maintainers)

These are set in repo settings; you don't configure them per PR:

1. **Secrets** (Settings → Secrets and variables → Actions):
   - `YANDEX_API_KEY`: Yandex Cloud API key for running Alice.
   - `YANDEX_PROJECT_ID`: Yandex project ID.
   - `ALICE_GITHUB_TOKEN` (optional): Fine-grained PAT scoped to this repo (Contents write, Pull requests write). If omitted, the workflow uses `GITHUB_TOKEN` but CI doesn't auto-run (manual trigger needed).

2. **Variables** (Settings → Variables):
   - `ALICE_AGENT_MODEL`: Model ID (default `aliceai-llm`, can be `aliceai-llm-pro`).

3. **Branch protection** (Settings → Branches → main/master):
   - Require PR reviews (agent PRs must be approved by a human).
   - Require CI to pass before merge.
   - Require branches to be up to date.

## Examples

### Example 1: Alice adds a docstring

Issue title: `@alice add docstring to compute_resources.py get_energy_pricing()`

Workflow runs:
- Runner creates invocation with issue text as prompt.
- Alice calls `write_file` to add docstring.
- Workflow:
  - Detects `changed_files: ["compute_resources.py"]`.
  - Runs `format.sh check`, `compileall`, `validate_runtime_modules.py`, `pytest`.
  - Opens PR `alice/issue-<N>-<RUN_ID>` with summary and cost (e.g., "Added docstring. Cost: 0.12 RUB, 450 tokens").
  - CI runs pytest; PR is draft until approved.

### Example 2: Alice needs human review

Issue title: `@alice integrate Jev tool router into mcp_mixin.py`

Workflow runs:
- Runner calls AliceClient with filesystem tools only.
- Alice detects she needs `network` or `execution` tool to test the integration.
- AliceClient.ask_with_mcp() returns pending tool (needs approval).
- Runner stops with `status: needs_approval`.
- Workflow posts summary to issue: "Stopped: pending tool call. Review the draft PR and approve manually if safe."

### Example 3: Alice touches .github/ (diff guard rejects)

Issue title: `@alice update alice.yml to use new model`

Workflow runs:
- Alice makes the change.
- Workflow checks diff: `.github/workflows/alice.yml` is touched.
- **Diff guard rejects**: workflow exits with error, no PR opened.
- Issue comment: "Rejected: agent must not modify `.github/` workflows. Please review the change manually."

## Related documentation

- [`docs/agents/alice-github-agent.md`](../docs/agents/alice-github-agent.md) — setup and operational guide for maintainers.
- [`AGENTS.md`](../AGENTS.md) — agent dispatch rules and shared patterns.
- [`tests/test_alice_agent_runner.py`](../tests/test_alice_agent_runner.py) — runner tests.
- [`tests/test_alice_workflow.py`](../tests/test_alice_workflow.py) — workflow security/structure tests.
- [`alice_agent_runner.py`](../alice_agent_runner.py) — headless runner implementation.
- [`.github/workflows/alice.yml`](.github/workflows/alice.yml) — GitHub Actions workflow.
- [`docs/agents/runtime-dispatcher-contract.md`](../docs/agents/runtime-dispatcher-contract.md) — how to delegate runtime work to agents.
