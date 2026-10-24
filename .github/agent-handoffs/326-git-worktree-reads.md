# #326 Git read compatibility handoff

Canonical scope: #326 recovered MCP interface: harmless allowlisted Git reads,
truthful read-only metadata and canonical executor routing. Existing accepted
contract: `tests/test_chatgpt_mcp.py::test_existing_git_read_tools_execute_through_mcp`.
The owner's repository-completion request authorizes correction of this regression.

Evidence: full validation of PR #1070 in a linked Git worktree failed that existing
MCP contract, independently of the Android changes. `_run_git` rejects a valid
worktree because `.git` is a file. Read commands also remove an index.lock older
than two seconds, contradicting their read-only contract.

Owner: Codex, Backend/Test Engineer, existing shell and GitHub connector; no agent
dispatch or model API. Stage: RED regressions → smallest implementation → PR.
Branch: `issue-326-git-worktree-reads`; base master
`51aaf8273f4f465fe78aa0a79e76a22d8f141e35`.
Output: retain Git's worktree detection, keep lock cleanup out of read operations,
real-Git regression tests and normal protected-PR publication. Keep original
read/write policy and UniversalToolExecutor; no additional MCP authority.

No production OAuth claim, device action, deployment or merge. Stop on changed
scope/head, failed deterministic checks or missing publication capability.
Broader #326 remains open for production client authentication/acceptance.
