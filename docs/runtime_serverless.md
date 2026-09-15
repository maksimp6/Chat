# Serverless + Sessioned Runtime

Alice Pro treats every `/api/chat` execution as an independent invocation.

## Lifecycle

```text
Session
  └── Conversation
        └── Invocation
              └── ExecutionTrace
```

- **Session** is the reusable runtime environment and persistent session metadata.
- **Conversation** is the message history and dialog-level configuration.
- **Invocation** is one execution of the pipeline.
- **ExecutionTrace** is the audit record for that invocation.

## InvocationContext

`InvocationContext` contains only request-scoped identifiers and metadata:
`session_id`, `conversation_id`, `invocation_id`, and `trace_id`.

It must not contain DB connections, global clients, mutable execution state, or a
reference to the trace itself. This keeps serialization safe and prevents the
circular-reference failure mode where `params["execution_trace"]` points back to
the trace.

## Persistence

The runtime schema stores sessions and invocations separately. Invocation status
moves through `created -> running -> completed|failed`. Results and errors are
stored as JSON for reload by a later serverless request.

## API

- `POST /api/sessions`
- `GET /api/sessions/<session_id>`
- `POST /api/sessions/<session_id>/invocations`
- `GET /api/invocations/<invocation_id>`

The existing chat endpoint remains unchanged in this phase. The next phase will
bind the existing Yandex/MCP/local-tool pipeline to `InvocationContext`, then
migrate `/api/chat` incrementally.
