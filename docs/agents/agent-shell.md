# Agent shell: starter

A minimal shell for running agent tasks in your own container instead of a GitHub
Actions job. This is the smallest slice: a task store, a runner and a CLI. It does not
yet start a model; handlers are plain Python functions registered by task kind.

## Pieces

- `agent_shell/store.py`: SQLite store of tasks and their events. A task is claimed
  atomically, so several runners can share one file.
- `agent_shell/runner.py`: runs one queued task through a handler. An unknown kind fails
  without running anything. A failure stores only a fixed code (`task_failed`,
  `unknown_kind`, `invalid_result`, `interrupted`), never the exception text, so a handler
  that touched a secret cannot leak it.
- `agent_shell/handlers.py`: built-in task kinds. The first, `cloudru_status`, is read-only
  and reuses `scripts/cloudru_check.py`.
- Approval gate: a task added with `--needs-approval` is `blocked` and is never picked
  until the owner runs `approve`. Use it for anything that deploys or touches secrets.

## Try it

```bash
python -m agent_shell add --role infra-engineer --title "Cloud.ru status" --kind cloudru_status
python -m agent_shell run-next      # needs the Cloud.ru keys in the environment
python -m agent_shell list
python -m agent_shell show 1        # result and event trace
```

`ALICE_SHELL_DB` (or `--db`) selects the database file. After a crash, run
`python -m agent_shell recover`: tasks left `running` are marked failed, never re-run.

## Not done yet

- No model or agent runs inside a task; only registered handlers.
- No per-task timeout: a handler that hangs blocks its runner.
- No scheduler: tasks are created by hand. A cron-style trigger should create tasks, not
  run agents directly.
- No container image or deploy for the runner; it runs wherever Python runs.
- Task states are separate from `agent_office/task_state.py` (GitHub dispatch evidence);
  merging the two is a later decision.
