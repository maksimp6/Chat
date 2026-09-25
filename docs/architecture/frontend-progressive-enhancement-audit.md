# Frontend progressive-enhancement audit

This document records the first focused slice of issue #227. The goal is to keep the server-rendered shell usable while making JavaScript enhancement independent from redundant lifecycle gates.

| Area | Current behavior | Problem | Can work without JS | Change | Risk | Test |
|---|---|---|---|---|---|---|
| templates/index.html | Main shell and memory controls are server-rendered; scripts use defer except the critical boot script | Large script list still contains legacy modules | Shell structure is present | Preserve HTML-first structure and keep critical boot synchronous | Low | frontend contract tests |
| static/models.js | Deferred script waits for DOMContentLoaded before binding controls | defer already guarantees parsed DOM; adds an unnecessary lifecycle dependency | Model selection itself requires JS | Initialize directly after script evaluation | Low | deferred-module contract |
| static/sidebar.js | Deferred script waits for DOMContentLoaded before binding and rendering | Same redundant lifecycle gate | Sidebar markup remains visible; navigation enhancement is JS | Initialize directly after script evaluation | Medium | deferred-module contract; CI |
| static/settings.js | Deferred script waits for DOMContentLoaded | Same redundant gate | Server shell remains usable | Initialize directly | Low | deferred-module contract |
| static/voice.js | Deferred script waits for DOMContentLoaded | Same redundant gate | Voice is optional enhancement | Initialize directly | Low | deferred-module contract |

## Ownership rule

- HTML/templates own structure, semantics, initial state and fallback markup.
- CSS owns layout and visual state.
- JavaScript owns enhancement, asynchronous operations, streaming and realtime behavior.
- Backend remains authoritative for validation, authentication and mutations.

## Lifecycle rule

A script loaded with defer is evaluated after HTML parsing. Modules in this slice therefore initialize immediately rather than registering another DOMContentLoaded callback. The change does not alter the existing dependency order in index.html.

The core/chat modules still contain broader initialization dependencies and are intentionally left unchanged in this slice. Removing their lifecycle gate without first defining an explicit cross-module readiness contract would change execution order rather than merely remove redundancy.

## Remaining audit work for #227

- inventory all inline handlers and remaining global mutable state;
- audit dynamically-created modal implementations and unsafe HTML construction;
- verify deep-link/history behavior and no-JS navigation paths;
- audit accessibility and focus management;
- measure initial HTML/critical CSS/JS request and byte budgets;
- execute the extreme-network preview scenario and collect trace evidence;
- audit service-worker/cache behavior and optional module failure isolation;
- add no-JS/E2E/accessibility coverage where backend routes permit it.
