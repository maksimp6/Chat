# ChatGPT Browser Tool contract

Issue: #751

This document defines the external tool boundary for the persistent Chrome Worker.
It is intentionally transport-neutral so the same browser operations can be
exposed to ChatGPT through an MCP/plugin adapter and to Alice Pro.

## Request path

```
ChatGPT -> MCP/plugin adapter -> Cloud.ru API Gateway -> Playwright API -> Chrome Worker
Alice   -> authenticated client -> Cloud.ru API Gateway -> Playwright API -> Chrome Worker
```

The Gateway is the supported public boundary. The worker must not expose CDP,
Chrome profile files, cookies, credentials, arbitrary JavaScript evaluation or
unrestricted shell execution.

## Stable operations

- `browser_status`: readiness, session identifier and safe page metadata.
- `navigate`: navigate the active page to an allowed HTTP(S) URL.
- `click`: click a locator in the active page.
- `type`: enter text into a locator. Secret values must be supplied through a
  protected credential mechanism, never ordinary tool arguments or traces.
- `extract`: return bounded structured/text page content.
- `screenshot`: return a bounded screenshot artifact/reference.

Each operation carries a generated request ID. Alice-originated calls may also
carry an ExecutionTrace correlation ID. Neither identifier is authorization.

## Authentication boundary

Browser operations use dedicated machine/service authentication at API Gateway.
They do not inherit Alice's browser cookie merely because Alice and the browser
worker share a project. ChatGPT and Alice are distinct clients.

The adapter stores/uses Gateway credentials; credentials are never returned by a
browser operation. Gateway logs, worker logs, Actions output and ExecutionTrace
must redact Authorization, Cookie, Set-Cookie, Google account data and extension
secrets.

## Chrome boundary

Playwright controls the installed Google Chrome Stable executable. CDP listens
only inside the worker and is never routed by API Gateway. The persistent Chrome
profile is private worker state. Extensions are installed from an explicit
allowlist/configuration and must not rely on Chrome Sync as their sole recovery
path.

## Initial HTTP mapping

The concrete API may evolve before production, but the first implementation
should keep a small versioned namespace:

- `GET /browser/v1/status`
- `POST /browser/v1/navigate`
- `POST /browser/v1/click`
- `POST /browser/v1/type`
- `POST /browser/v1/extract`
- `POST /browser/v1/screenshot`

Do not add a catch-all browser proxy. Every externally callable browser operation
must be explicitly declared in the Gateway contract and covered by authorization,
input bounds, rate limits and tests.

## First acceptance flow

1. External MCP/plugin client calls `browser_status` through API Gateway.
2. It navigates Chrome to a synthetic test page.
3. It clicks/types into non-secret controls.
4. It extracts bounded page content.
5. It obtains a screenshot reference.
6. A controlled worker restart restores the same private profile.
7. Direct anonymous access to worker control routes fails.
8. Logs/traces contain no cookies, credentials or profile data.
