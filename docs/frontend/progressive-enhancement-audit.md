# Frontend progressive enhancement audit

Issue: #227
Baseline commit: 3a5630be72303877bc396a5605efc4b9eca3ccde
Audit date: 2026-09-25

This is the initial evidence-based audit before the next implementation slice. It records findings from the current master tree after #310 and #313.

| Area | Current behavior | Problem | Can work without JS? | Proposed solution | Risk | Test |
|---|---|---|---|---|---|
| templates/index.html | Useful app shell, chat input and controls are server-rendered | Several controls still have inline presentation styles; critical controls are mostly buttons without server fallback | Partly | Keep semantic controls, remove avoidable inline presentation from critical shell | Medium | Template contract + no-JS smoke |
| Script loading | Boot is synchronous; many enhancement scripts are deferred and loaded individually | Large deferred dependency surface and implicit global ordering | N/A | Classify critical vs enhancement modules and make initialization explicit | High | Script contract + failure isolation |
| static/core.js | DOMContentLoaded owns most startup and loads conversations/models/history | Core startup depends on many globals and remote requests before full enhanced state is ready | Shell yes, enhanced chat no | Isolate startup lifecycle and retain server-rendered shell during failures | High | Init/re-init + API failure |
| static/sidebar.js | JS builds conversation list and handles selection/history | Full list uses innerHTML; conversation items are clickable divs rather than links | Navigation can be HTML-first if backend route supports it | Use links/buttons with explicit semantics; replace avoidable full DOM rebuilds | Medium | Keyboard + deep-link/history |
| static/chat.js | JS renders approval cards | Uses innerHTML and inline styles for dynamic UI | No for live approval flow, but fallback/error state can be semantic | Build nodes safely and move presentation to CSS | Medium | Security/DOM contract |
| static/models.js | DOMContentLoaded binds model modal | Initialization is lifecycle-bound and assumes globals from other modules | No for model selection UI | Explicit idempotent initializer and module ownership | Medium | Re-init + missing DOM |
| static/android_diagnostics.js | DOMContentLoaded plus timeout installs diagnostics UI | Timer is used as lifecycle fallback and optional diagnostics are in page load path | Yes, diagnostics are optional | Make diagnostics independently failure-isolated and non-critical | Low | Optional module failure |
| static/trace_viewer_auto.js | MutationObserver watches document body | Global observer can react to unrelated DOM changes | Yes for baseline | Scope observer and make initialization/disposal explicit | Medium | Observer lifecycle |
| static/treasury.js | Adds document keydown handler when modal opens | Listener ownership is tied to modal state and needs careful cleanup | No for interactive treasury modal | Keep one owned handler and cleanup on close | Medium | Reopen/re-init duplicate listener test |
| static/provider_credentials.js | Builds UI and opens modal through global function | Direct style mutation and DOMContentLoaded initialization | No for provider credential management | Move presentation to CSS/state classes and isolate optional UI | Medium | Missing DOM/module failure |
| CSS | Layout is primarily CSS, but file contains duplicated Markdown rules and legacy/debug comments | Dead/conflicting rules increase maintenance risk | Yes | Consolidate only proven duplicates during focused slices | Low/Medium | Visual smoke |
| State | Conversation/theme state is split between globals, URL and localStorage | Multiple authorities can diverge | N/A | Document ownership; server/URL authoritative where applicable, storage only preference/cache | High | Refresh/history/storage corruption |
| URL/history | Conversation URL and popstate are handled client-side | Direct navigation is not clearly guaranteed to render useful conversation server-side | Partly | Verify backend direct URL behavior before changing routing | High | Deep-link/reload/back-forward |
| Service worker/cache | Boot unregisters legacy service workers/caches | Cleanup is asynchronous and can run on every page load | Yes | Verify there is no active required SW before retaining cleanup | Medium | Cache/SW regression |
| Accessibility | Some buttons have labels, others rely on visible glyphs/title | Keyboard/focus/dialog semantics are inconsistent | Yes for shell | Audit names, focus, modal semantics and links | Medium | Automated a11y + keyboard smoke |
| Security | Dynamic chat UI contains innerHTML interpolation | User/tool-controlled strings can become HTML if not escaped | No | Prefer DOM APIs/textContent for untrusted values | High | XSS regression test |

## Current loading/ownership map

1. boot.js is the synchronous compatibility/bootstrap layer.
2. file_manager.js, treasury.js, settings modules, core.js, theme/sidebar/models/chat/trace/voice and diagnostics are deferred.
3. Several modules still register DOMContentLoaded handlers independently.
4. Some modules expose global functions consumed by other modules, so load order is an implicit dependency.
5. eruda_init.js is optional/debug functionality and must never be a critical-path dependency.
6. Memory controls were already moved to an HTML-first/idempotent implementation by #313 and are intentionally not repeated here.

## Immediate implementation slice

The first implementation slice after this audit should:
- make the critical shell independent of optional/debug initialization;
- make optional module failures isolated;
- remove avoidable inline presentation from the critical template;
- add regression contracts for script ordering and optional-module isolation;
- then proceed to navigation/forms and module lifecycle refactors with focused tests.

## Evidence still required

The repository audit alone does not establish the runtime root cause of a blank screen. The following require an actual Preview/browser run:
- navigation commit;
- shell visibility at ~250 ms, 1 s, 3 s and 5 s;
- #app-root and #msg-input visibility;
- FCP/paint/navigation timing;
- HTTP failures;
- screenshot and Performance trace if blank state occurs;
- planned extreme profile: 10 KiB/s down, 0.065 KiB/s up, +2500 ms latency.

No runtime claim is made here until that evidence is collected.