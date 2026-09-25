# Frontend progressive-enhancement audit

Issue: #227  
Base: `master` at `3a5630be72303877bc396a5605efc4b9eca3ccde`  
Audit branch: `refactor/frontend-progressive-audit`

## Scope

Reviewed the current server-rendered shell and the main frontend entry modules on the audit branch:

- `templates/index.html`
- `static/boot.js`
- `static/chat.js`
- `static/core.js`
- `static/sidebar.js`
- `static/sw.js`
- `static/style.css`
- `static/eruda_init.js`
- `static/header_actions.js`
- `static/departments.js`
- `static/memory_panel.js`
- `static/trace_viewer_auto.js`

The remaining `static/*.js` modules are part of the follow-up ownership review. This document records the first audit gate before broader refactoring.

## Findings

| Area | Current behavior | Problem | No-JS possible? | Proposed solution | Risk | Test |
|---|---|---|---|---|---|---|
| Initial shell | `#app-root`, chat shell, input and header are server-rendered | Good baseline, but conversation list/content are client populated | Yes for shell | Preserve server-rendered shell and avoid moving critical structure into JS | Low | HTML contract |
| Script loading | Many independent `defer` scripts are listed in `index.html` | Dependency/order is implicit and duplicated globals are possible | N/A | Document ownership and make optional modules failure-isolated | Medium | Script contract |
| Boot | `boot.js` installs fetch/EventSource URL prefixing and legacy SW/cache cleanup | Global monkey-patching is critical infrastructure | N/A | Keep narrowly scoped, test URL rewriting and failure isolation | Medium | Boot contract |
| Eruda | `eruda_init.js` asynchronously injects the local ~500 KiB bundle after DOM startup | Debug tooling adds a very large optional asset | Yes | Keep completely outside critical UI path; later measure and gate/debug-load only | Low | Performance/network test |
| Trace export | `trace_viewer_auto.js` dynamically injects `trace_download.js` | Optional feature has no explicit lifecycle/error boundary | Yes | Make optional integration explicitly isolated and idempotent | Medium | Module failure test |
| Sidebar | JS renders conversation items and owns history handling | Baseline navigation is client-only for conversation selection | Partly | Add real conversation URLs/server-rendered links where backend permits; retain enhanced history | High | Deep-link/no-JS E2E |
| Sidebar storage | Conversations/current conversation are copied to localStorage | Browser storage duplicates server state | No for authoritative data | Treat localStorage as cache only and document ownership/schema | Medium | Storage contract |
| Sidebar lifecycle | DOMContentLoaded initialization with direct listeners | Re-init can duplicate listeners | N/A | Add idempotent initializer/ownership marker | Medium | Re-init test |
| Departments | Modal and rows are created dynamically | Feature has no server-rendered fallback | No, complex enhancement | Keep enhancement but isolate missing-DOM/API failures | Medium | Missing DOM/API test |
| Memory | HTML-first modal and idempotent binding | Already aligned with #227 after #313 | Yes for structure | Preserve current contract | Low | Existing memory contract |
| Chat rendering | Markdown and approval cards use `innerHTML` after partial escaping | Large dynamic DOM surface; inline presentation styles remain | No for live chat | Prefer DOM APIs for security-sensitive fragments and move presentation to CSS incrementally | Medium/High | XSS/render tests |
| CSS | Layout is primarily CSS | Some duplicate Markdown rules and legacy presentation remain | Yes | Consolidate only with behavior-preserving tests | Medium | Visual/smoke tests |
| Service worker | Cache-first handler caches a fixed set of root/static assets | Can serve stale shell/assets and ignores preview/base paths | No | Audit registration and preview behavior before changing cache policy | High | SW/cache tests |

## Ownership map

### Templates / HTML

Own:

- application landmarks and shell structure;
- labels, buttons and links;
- initial empty state;
- server-provided configuration;
- accessible names and fallback structure.

Critical elements already present server-side:

- `#app-root`
- `#chatbox`
- `#msg-input`
- primary navigation/action buttons.

### CSS

Own:

- app layout;
- responsive behavior;
- modal presentation;
- visual states;
- reduced-motion and focus styling.

### JavaScript

Own:

- enhanced conversation switching/history;
- async API operations;
- streaming/realtime behavior;
- optional tools and debug integrations;
- transient UI state.

Every module should have one initialization owner and tolerate missing optional DOM.

### Backend

Own:

- authoritative conversation state;
- validation;
- authorization;
- mutations;
- server fallback where ordinary HTTP semantics are available.

## Immediate refactoring priorities

1. Preserve the useful server-rendered shell.
2. Make module initialization idempotent and explicitly owned.
3. Separate optional/debug modules from the critical path.
4. Replace client-only navigation with real URLs where backend support exists.
5. Reduce browser-storage duplication of authoritative state.
6. Move inline presentation from JavaScript/template attributes into CSS.
7. Audit the service worker before changing application caching.
8. Add no-JS/deep-link/accessibility contracts before larger frontend changes.

## Known limitations of this audit

No preview performance run was performed in this commit. No claim is made about the root cause of the extreme-network blank-screen observation yet. That requires the prescribed 10 KiB/s down + 0.065 KiB/s up + 2500 ms latency run with early milestones, screenshot and Performance trace.

No-JS behavior has not yet been declared complete. The current sidebar and chat runtime still depend on JavaScript for enhanced conversation behavior.

## Extreme-network gate

The issue's established baseline remains authoritative:

- observed manual profile: 10 KB/s download, 10 KB/s upload, 200 ms latency;
- planned stress profile: 10 KiB/s download, 0.065 KiB/s upload, 2500 ms additional latency;
- runner throughput units: KiB/s converted to bytes/s.

Before changing the critical path, capture navigation commit, 250 ms / 1 s / 3 s / 5 s shell state, `#app-root` / `#msg-input` visibility, paint/navigation timing, failures and HTTP >= 400, and a screenshot/Performance trace for any blank state.

## Next implementation slice

The next slice should target **module lifecycle and optional-module isolation**, not a broad rewrite. The memory panel is already covered by #313 and should not be reimplemented.
