# Background tasks

Long-running work does not run in threads of the web process. A handler
enqueues a job in a durable queue and returns; a task worker claims the job,
runs it and records progress events and the result. The web container can then
be stopped, restarted or scaled to zero on Cloud.ru Container Apps without
losing accepted work (issue #444, epic #440).

## Pieces

- `tasks/queue.py`: the queue contract and its backends.
- `tasks/registry.py`: `@register_task("kind")` handlers receive a
  `TaskContext` with the payload and `emit(event)` for progress.
- `tasks/worker.py`: the worker loop, the inline mode and the CLI.

Jobs currently handled: `voice.process` (STT, chat, TTS for `/api/voice/*`).

## Queue backends

| `ALICE_TASK_QUEUE` | Storage | Use |
| --- | --- | --- |
| `sql` (default) | `task_jobs` and `task_events` in the app database (SQLite or PostgreSQL via `ALICE_DATABASE_URL`) | local, Termux, single container |
| `redis` (default when `ALICE_REDIS_URL` is set) | Redis keys under `alice:tasks:` | cloud, Cloud.ru Managed Redis |
| `memory` (default with `ALICE_DB_BACKEND=memory`) | process memory, not durable | tests |

A claimed job is leased to one worker (`ALICE_TASK_LEASE_SECONDS`, default
900). If the worker dies, the lease expires and another worker picks the job up
again, up to three attempts; after that the job is marked failed and the voice
event stream reports the error. Finished jobs are purged after
`ALICE_TASK_RETENTION_SECONDS` (default 3600).

## Running the worker

`ALICE_TASK_WORKER` decides who runs jobs:

- `inline` (default): the web process starts one worker thread on the first
  enqueue. This keeps local and Termux installs working with one command.
- `external`: the web process only enqueues. Run a worker from the same image:

```sh
python -m tasks           # long-running worker, polls every ALICE_TASK_POLL seconds
python -m tasks --drain   # exit when the queue is empty (Container Apps Jobs)
```

With several web replicas or scale to zero, use `ALICE_TASK_WORKER=external`,
a shared queue (`ALICE_REDIS_URL` or PostgreSQL) and a separate worker.

## Environment runtimes

Branch environment runtimes (`environment_manager.py`) still execute as threads
of the process that started them, because they serve HTTP from loaded code. Their
ownership is no longer only in memory: each `RUNNING` row stores
`runtime_instance` and a `runtime_heartbeat_at` refreshed every 15 seconds. On
startup an instance marks as `STOPPED` only runtimes whose owner is itself, is
unknown, or has not sent a heartbeat within `ALICE_RUNTIME_LEASE_SECONDS`
(default 60). Requests for a runtime that lives on another live instance fail
with an error naming that instance instead of silently resetting it.
`ALICE_INSTANCE_ID` overrides the generated instance id.
