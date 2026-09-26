# Codex task: finish PR #338 safely

Repository: `maksimp6/Chat`

Work only on the existing branch:

`fix/frontend-dispatcher-contract`

Existing pull request:

`#338 test: enforce dispatcher-only frontend execution`

Do not create a new branch or a new pull request.

## Current state

The security and frontend work is already largely complete.

Current protections/results:

- Format workflow is green.
- CodeQL workflow is configured and green for Python and JavaScript/TypeScript.
- Preview deployment is green.
- PostgreSQL integration is green.
- Android debug APK is green.
- Application tests are the only remaining blocker.
- CodeQL is a required repository rule. Do not disable, bypass, weaken, or remove it.
- Production coverage policy must remain in place.
- Dispatcher-only frontend transport/timer policy must remain in place.
- Do not reintroduce direct `fetch` or direct timer ownership into feature modules.

## Why Application tests fail

The latest Application tests run has four failures caused by the security hardening that stopped returning raw Python exception messages to API clients.

Failures:

1. `test_chat_sqlite_integration.py::TestChatSQLiteIntegration::test_partial_output_and_trace_survive_sqlite_reload`

   The old test expects the internal exception text:

   `Failure after model response`

   The API now intentionally returns the safe public message:

   `Внутренняя ошибка обработки запроса`

2. `test_partial_output.py::TestActiveChatRoute::test_partial_output_is_returned_and_persisted_when_pipeline_fails`

   Same issue. The test still expects the raw internal exception text in the user-facing reply.

3. `tests/test_conversation_routes.py::test_list_conversations_rejects_invalid_owner_token`

   The old test expects the raw exception message:

   `invalid authenticated owner token`

   The API now intentionally returns the stable public error code:

   `invalid_owner_identity`

4. `tests/test_runtime_api.py::test_runtime_api_rejects_request_contract_drift`

   The old test expects the internal validation detail:

   `unexpected fields: unexpected`

   The API now intentionally returns the stable public message:

   `request payload does not match API contract`

## Required fix

Update the affected tests and, where appropriate, named API contracts so they validate the new security boundary instead of requiring raw backend exception text.

Do not restore `str(exc)` or raw Python exception messages in JSON responses.

The desired contract is:

- server logs may contain diagnostic exception details;
- persisted internal ExecutionTrace may retain sanitized technical diagnostics according to existing trace security rules;
- public API responses must use stable error codes/messages;
- user-facing replies must not expose arbitrary backend exception strings or stack details.

### Chat failure contract

For generic chat pipeline failures:

- preserve partial output produced before the failure;
- persist the trace and partial output;
- return a stable safe public error;
- user-facing reply may contain the partial output plus the safe failure message;
- raw exception text must not be required in the public response.

The two partial-output regression tests must continue proving that:

- partial output survives;
- it is persisted to SQLite;
- invocation/trace persistence survives;
- HTTP status remains correct;
- the safe public error is present.

Add an assertion that the raw internal error text is NOT exposed by the HTTP response.

### Invalid owner identity contract

For invalid authenticated owner identity:

- return HTTP 401;
- public error code is `invalid_owner_identity`;
- do not require the raw exception message.

Update the route test accordingly.

### Runtime API contract violation

For invalid request payload:

- return HTTP 400;
- `error` remains `invalid_request`;
- public message is `request payload does not match API contract`;
- do not echo raw validator exception text or user-controlled field/value data.

Update the contract regression test accordingly.

## Security regression coverage

Add or strengthen tests proving that raw exception strings are not returned from the affected endpoints.

At minimum cover:

- generic chat pipeline exception;
- conversation owner identity exception;
- runtime ContractViolation;
- provider health-check exception response where practical.

Tests should assert stable public error codes/messages and the absence of a known internal exception marker.

Do not test this by searching source strings if the behavior can be exercised through Flask test clients.

## Prototype pollution fix

Keep the current `SettingsUI.deepMerge` hardening:

- ignore inherited properties;
- ignore `__proto__`;
- ignore `prototype`;
- ignore `constructor`;
- preserve normal nested object merging.

Keep `tests/test_settings_ui_security.js` in Application tests.

Do not remove this fix merely to make CodeQL quiet.

## CodeQL

The repository now has `.github/workflows/codeql.yml`.

It scans:

- Python
- JavaScript/TypeScript

The workflow stores SARIF artifacts for diagnostics.

Do not remove the workflow or reduce its language coverage.

If CodeQL reports a new blocking alert, fix the root cause. Do not change the repository rule or severity threshold.

## Coverage

Keep the production-only coverage configuration.

Current production baseline gate is based on the measured production-only baseline. Do not lower it to make CI pass.

Keep strict diff coverage for new Python executable lines.

If changed Python lines need tests, add meaningful behavior tests.

## Required validation

Run locally where available:

```bash
bash scripts/format.sh write
bash scripts/format.sh check

node tests/test_settings_ui_security.js
node tests/test_model_catalog_diagnostics.js

pytest -q \
  test_chat_sqlite_integration.py \
  test_partial_output.py \
  tests/test_conversation_routes.py \
  tests/test_runtime_api.py
```

Then run the complete available test suite.

After pushing to the same branch, inspect GitHub Actions.

Required final state:

- Format: green
- Application tests: green
- PostgreSQL integration: green
- Android debug APK: green
- Preview deployment: green
- CodeQL (Python): green
- CodeQL (JavaScript/TypeScript): green
- repository merge rules satisfied

If a check fails:

1. open the failed job;
2. read the exact failing step/log;
3. fix the root cause;
4. push to the same branch;
5. repeat until green.

Do not stop after reporting a failed check when the repository or CI log provides enough information to continue.

## Final action

When all required checks and repository rules are satisfied:

- update PR #338 description with the final security/CI result;
- do not create another PR;
- leave the branch merge-ready.

Do not bypass CodeQL and do not restore raw exception exposure just to satisfy old tests.
