# Execution Surface

Issue: #537

`static/execution_surface.js` (`window.LogosExecutionSurface`) projects a
sanitized ExecutionTrace into one read-only schema, `logos.execution_surface.v1`.
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
prompts, payloads, tool arguments or credentials. The former BrowserShim canary
test has been removed; it is no longer evidence for this contract. Check live
claims against the Redmi 9 rendered surface and saved traces.

Chat replies that carry a trace show the compact view (`.execution-surface`)
above the existing detailed timeline (`LogosTraceSummary`).
