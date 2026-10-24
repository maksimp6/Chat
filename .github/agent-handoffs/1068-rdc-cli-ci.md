# #1068 / PR #1070 CLI correction handoff

Canonical issue: https://github.com/maksimp6/Chat/issues/1068.
Existing deliverable/accepted offline contract: PR #1070, restricted ADB actions,
no root/arbitrary shell, synthetic tests and submitted-versus-verified status.

Primary owner: Codex, Test/Backend Engineer, existing shell and GitHub connector.
Stage: reproduce exact-head CI → correction → validation → update existing draft.
Worktree: `/workspace/Chat-1070`, branch `issue-1068-rdc-ci-fix`.
Original PR head: `22840b6e89883653856a6813395d1d1eb07f5ffb`.
Prepared head: `9ea59332e5b2c22bb8b34ac99412084d513701d7`, normal merge of current
master `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`; original head remains an ancestor.

Root cause: run 37957109587, Code rules job 113910311895 rejects the newly typed
`tools/android/rdc_actions.py` because it is absent from STRICT_CLEAN_FILES.
Reproduced locally: 1 failed / 8 passed. Keep all typing/coverage gates intact.
Output: register the clean module, fail closed on unknown actions and missing
executables/filesystem failures, add offline error regressions, publish to the
existing PR branch after the canonical formatting gate.

Approval: owner's repository-completion request covers code/tests and publication.
Do not connect to a device, inject input, restart RDC or claim device E2E.
Stop on changed PR head/contract, failed tests/gate or rejected publication.
Device acceptance, independent review and explicit merge authorization remain
required by the existing PR; keep it draft and do not merge.
