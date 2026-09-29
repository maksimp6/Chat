# Async/concurrency runtime contract

This document records the concurrency semantics currently proven by Alice Pro tests.
It is a contract for the existing sync + threadpool runtime, not a promise to rewrite
the backend around native asyncio.

## Current execution model

The backend remains primarily synchronous. Production concurrency currently comes
from thread pools, concurrent invocations, database transactions, agent execution,
quota reservations, budget reservations, and request/tool lifecycles.

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

Crossing an `await` or `asyncio.to_thread` boundary must not leak another
invocation's context. Persisted traces must keep the same identifiers that were
active when the invocation was created.

Process-global mutable state is not a substitute for request/runtime context.

## Cancellation semantics

Cancelling an asyncio waiter does not imply that the underlying worker thread has
stopped. Code must distinguish:

1. cancellation of the caller/waiter;
2. cancellation of the domain operation;
3. physical termination of a running thread or provider operation.

For invocation lifecycle state, cancellation is explicit and fail-closed:
a cancelled invocation must remain cancelled, and a worker that finishes later
must not overwrite it with `completed`.

Sibling invocations/tasks must remain independent. Cancelling one waiter must not
corrupt another invocation, session, trace, quota bucket, or budget account.

## Timeout semantics

`AgentGateway` timeout means the gateway has stopped waiting for the handler and
returns an error result. A timed-out worker may still be running until its thread
finishes or is otherwise released.

Required behavior:

- timeout result is `error`, never a false `completed`;
- sibling agent calls remain able to complete while the timed-out worker is still
  alive;
- timeout accounting is based on observed timeout behavior, not arbitrary sleeps;
- tests must synchronize on the timeout transition when proving ordering.

Native cancellation of running Python threads is not part of the contract.

## Budget reservation atomicity

`BudgetController.reserve()` is serialized by the controller lock. Concurrent
reservations must never oversubscribe the same account balance.

The async harness must force real contention rather than relying on scheduler
luck. The regression suite includes a mutation-control that removes the lock and
deterministically reproduces oversubscription. This proves that the concurrency
test can detect the missing-lock race it is intended to protect against.

Reservation, settlement, release, spend, refund, cooldown, and fallback semantics
remain domain operations owned by `BudgetController`, not by the LLM.

## Provider quota atomicity

`provider_quotas.reserve_request()` is the provider-request admission boundary.

Atomicity requirements differ by backend:

- SQLite uses `BEGIN IMMEDIATE`;
- PostgreSQL locks the user's usage row with `SELECT ... FOR UPDATE`.

Concurrent async callers must not spend the same quota slot twice. The async
harness executes this path through `asyncio.to_thread` and is run by both the
SQLite/Application CI path and the PostgreSQL CI path.

Tests that claim PostgreSQL coverage must not force `db.DB_PATH` to a temporary
SQLite database when `ALICE_DATABASE_URL` is active.

## Task cleanup

Async harnesses must leave no owned pending asyncio tasks after completion.
A test that merely `gather()`s tasks and then asserts those same task objects are
done is insufficient; the suite compares task sets before and after the operation
to detect newly leaked pending tasks.

Thread cleanup is a separate concern from asyncio task cleanup.

## Database/backend expectations

Concurrency tests that touch shared durable state must preserve backend semantics.

- Local/default tests may use isolated SQLite files.
- PostgreSQL CI must exercise the real PostgreSQL code path when that is the
  behavior under test.
- Do not silently substitute SQLite in tests that claim row-lock or transaction
  coverage for PostgreSQL.
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

- concurrent invocation/context/trace isolation;
- cancellation without sibling corruption;
- AgentGateway timeout isolation;
- leaked asyncio task detection;
- budget reservation contention and mutation-control;
- provider quota reservation atomicity;
- SQLite and PostgreSQL CI execution.

Relevant commands:

```bash
pytest -q tests/test_async_runtime_concurrency.py
pytest --durations=30 -q
```

The ordinary protected CI remains authoritative for PostgreSQL and full-suite
validation.
