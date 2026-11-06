# Async/concurrency runtime contract

This document records the concurrency semantics currently proven by Logos tests.
It is a contract for the existing sync + threadpool runtime, not a promise to rewrite
the backend around native asyncio.

## Current execution model

The backend remains primarily synchronous. Production concurrency currently comes
from thread pools, concurrent invocations, database transactions, agent execution,
and request/tool lifecycles.

Async tests use `asyncio` as an orchestration harness around those existing
boundaries, usually through `asyncio.to_thread(...)`. This lets tests reproduce
production-like interleavings without changing the runtime API simply to make it
look asynchronous.

Do not convert a stable sync boundary to native async unless there is a concrete
I/O, cancellation, throughput, or lifecycle benefit that cannot be obtained with
the existing thread-based implementation.

## Context and trace isolation

Concurrent tasks must preserve independent:

- `invocation_id`;
- `trace_id`;
- `conversation_id`;
- session ownership;
- bound `ExecutionTrace` context.

Crossing an `await` boundary must not leak another invocation's bound trace
context. The regression suite proves event-loop task isolation, persisted trace
correlation when work is delegated with `asyncio.to_thread(...)`, and implicit
`ContextVar` propagation by reading `get_current_trace()` inside worker threads.
Concurrent workers must observe only the trace bound by their own originating
task.

Process-global mutable state is not a substitute for request/runtime context.

## Cancellation semantics

Cancelling an asyncio waiter does not imply that the underlying worker thread has
stopped. Code must distinguish:

1. cancellation of the caller/waiter;
2. cancellation of the domain operation;
3. physical termination of a running thread or provider operation.

For invocation lifecycle state, cancellation is explicit. The regression suite
proves that cancelling one asyncio waiter does not corrupt a sibling invocation,
that an already-cancelled invocation rejects a later duplicate cancellation
transition, and that a late worker calling `finish_invocation()` after external
cancellation cannot overwrite terminal `cancelled` state with `completed`.

Sibling invocations/tasks must remain independent. Cancelling one waiter must not
corrupt another invocation, session, or trace.

## Timeout semantics

Native cancellation of running Python threads is not part of the contract.

## Transaction coverage

Tests that claim transaction coverage must exercise the real native Kristall store
in a temporary journal, not a substitute, and must not reintroduce
`LOGOS_DATABASE_URL`.

## Task cleanup

The async test harness snapshots the event-loop task set before and after both
short-lived harness tasks and representative invocation lifecycle work executed
through `asyncio.to_thread(...)`. Newly created pending tasks must not remain
after the operation completes.

This proves cleanup for the covered runtime pattern; it is not a universal claim
that arbitrary future native-async code cannot leak tasks. New task-owning
boundaries still require focused before/after cleanup coverage.

Thread cleanup is a separate concern from asyncio task cleanup.

## Database/backend expectations

Concurrency tests that touch shared durable state must preserve native store semantics.

- Local/default tests use isolated native Kristall journals in a temporary directory.
- Tests that claim transaction or write-barrier coverage must exercise the real
  `kristall.Kristall` path under the one-writer rule, not a substitute.
- SQLite and PostgreSQL are retired and are not test backends (see `docs/database.md`).
- Existing thread-race tests remain required; async tests complement them.

## ExecutionTrace and tool-loop boundaries

ExecutionTrace correlation must survive task/thread boundaries. Tool execution
still follows the existing canonical execution boundaries, including
`UniversalToolExecutor`, invocation lifecycle state, and runtime scoping.

Moving a tool or provider call to native async does not relax trace, authorization,
redaction, runtime ownership, or fail-closed requirements.

## Native async decision

Keep a boundary sync + threadpool when:

- the implementation is stable and deterministic;
- the underlying library/API is synchronous;
- cancellation cannot safely stop the underlying operation anyway;
- throughput is already acceptable;
- changing the boundary would mostly change syntax rather than semantics.

Consider native async when:

- the underlying I/O library is natively async;
- high concurrent wait time dominates execution;
- cancellation/timeout must propagate through the public API;
- WebSocket/streaming behavior materially benefits from task-based orchestration;
- lifecycle ownership can be expressed more clearly with structured concurrency.

Any migration to native async must first preserve the regression contract in this
document, then add focused tests for the new cancellation and cleanup behavior.

## Required validation

The current regression surface includes:

- concurrent invocation/trace persistence isolation across event-loop tasks;
- worker-side `ContextVar` / bound ExecutionTrace propagation through `asyncio.to_thread`;
- cancellation without sibling corruption;
- late `finish_invocation()` after external cancellation cannot overwrite terminal state;
- representative invocation lifecycle task cleanup;
- native Kristall transaction execution (SQLite and PostgreSQL are retired).
