# Execution Surface

Issue: #537

`static/execution_surface.js` (`window.AliceExecutionSurface`) projects a
sanitized ExecutionTrace into one read-only schema, `alice.execution_surface.v1`.
The frontend keeps no execution state of its own. The projection holds:

- `state`: `working`, `blocked`, `done`, `partial`, `error`, `timeout` or
  `cancelled`. It is derived from response statuses, errors, blocking events
  (quota, tool access, approval) and trace completion.
- `stages`: request, model, tools (when used) and finish, each `done`, `active`
  or `pending`.
- `latest_event`, `blocker`, `models`, `model_calls`, `tool_calls`, `duration_ms`.
- `usage`: `measured` token counts (billing items or response usage, cached
  tokens included), otherwise `unknown`.
- `cost`: `calculated` or `partial` (a tariff calculation from billing),
  otherwise `unknown`.

Nothing is invented. A missing value stays `null`/`unknown`, and the compact
view shows it as "неизвестно". Only whitelisted metadata is copied, never
prompts, payloads, tool arguments or credentials. `tests/test_execution_surface.js`
enforces this with a canary.

Chat replies that carry a trace show the compact view (`.execution-surface`)
above the existing detailed timeline (`AliceTraceSummary`).
