# Frontend Loading Baseline

**Issue:** #227  
**Baseline ref:** `master`  
**Baseline commit:** `d4ad1b154f7822f48e613646bcdff7a94285c13d`  
**Audit type:** repository/static loading audit  
**Date:** 2026-09-20

## 1. Scope

This baseline describes the current browser entrypoint and the resources referenced by the main HTML shell. It is the reference point for later performance changes.

The audit intentionally does not modify the existing application shell. Runtime browser measurements that require a live browser session are marked as pending and must be captured with DevTools/Playwright against the deployed preview.

## 2. Current frontend architecture

The current `master` branch is a server-rendered Flask application. The browser entrypoint is:

`templates/index.html`

There is no root `frontend/` directory and no root `package.json` on `master`. The frontend is therefore currently served as a collection of static JavaScript/CSS files rather than a Vite/Rollup application.

The HTML shell references:

- 23 deferred JavaScript files;
- 2 CSS files;
- the favicon twice.

All application scripts use `defer`, while the two stylesheets are regular stylesheets and therefore remain part of the initial render path.

## 3. Static size baseline

Repository blob sizes at the baseline commit:

| Resource group | Count | Bytes |
|---|---:|---:|
| HTML entrypoint | 1 | 10,852 |
| JavaScript in `static/` | 30 | 713,390 |
| CSS in `static/` | 2 | 18,747 |
| JS + CSS in `static/` | 32 | 732,137 |
| JS directly referenced by the entrypoint | 23 | 197,535 |
| Eruda bundle | 1 | 500,190 |

The 500,190-byte Eruda bundle is not directly referenced by `index.html`. It is injected asynchronously by `static/eruda_init.js` after DOM startup and only needs to be considered when debugging is requested/initialization runs.

## 4. Initial resource graph

Current entrypoint order:

```text
HTML
 ├─ style.css
 ├─ favicon.svg
 ├─ favicon.svg
 ├─ boot.js (defer)
 ├─ file_manager.js (defer)
 ├─ treasury.js (defer)
 ├─ settings/ui_helpers.js (defer)
 ├─ settings/settings_storage.js (defer)
 ├─ settings/settings_mcp.js (defer)
 ├─ settings/settings_params.js (defer)
 ├─ settings/settings_modal.js (defer)
 ├─ settings.js (defer)
 ├─ core.js (defer)
 ├─ sidebar.js (defer)
 ├─ models.js (defer)
 ├─ chat.js (defer)
 ├─ trace_viewer.js (defer)
 ├─ trace_viewer_timing_fix.js (defer)
 ├─ trace_viewer_error_ui.js (defer)
 ├─ trace_viewer_auto.js (defer)
 ├─ voice.js (defer)
 ├─ android_diagnostics.js (defer)
 ├─ eruda_init.js (defer)
 ├─ departments.css
 ├─ departments.js (defer)
 ├─ memory_btn.js (defer)
 └─ cloudru_iam.js (defer)
```

The shell also contains a substantial inline bootstrap block that:

- establishes preview/static paths;
- patches `fetch`;
- patches `EventSource`;
- injects the anonymous-user header when available.

The current document also contains inline memory-management UI and inline memory-management JavaScript.

## 5. Important observations

### 5.1 JavaScript is split into many independent requests

There are 23 initial script tags. Although they are deferred, they still form a broad initial resource graph and must be evaluated together before the application is fully interactive.

Later work should identify the smallest set required for:

1. rendering the shell;
2. showing the main chat UI;
3. accepting the first interaction.

Everything else should be moved out of the critical path where safe.

### 5.2 Debug tooling is already designed as optional

`eruda_init.js` dynamically injects `eruda.js` and retries loading after a failure. This is a useful pattern to preserve: diagnostic tooling must not become a dependency of the application shell.

### 5.3 Cache behavior is intentionally conservative

`app.py` currently sets:

- HTML `/` to `Cache-Control: no-store, max-age=0`;
- `/static/*` to `Cache-Control: no-cache` unless another value already exists.

The same file calculates a static asset version token from asset mtimes unless `ALICE_STATIC_VERSION` is configured.

This is reliable for avoiding stale dependency graphs, but it is also a likely optimization target later because production assets currently cannot take full advantage of immutable caching.

### 5.4 Preview deployment already exists

`.github/workflows/preview-deploy.yml` deploys an isolated preview for pull requests and verifies:

- preview health;
- public preview availability;
- core web assets.

This gives Issue #227 a suitable place for repeatable browser profiling once instrumentation/tests are added.

### 5.5 Visual continuity must be measured, not assumed

The requirement for transitions from the primary shell to additional modules is part of the performance baseline.

Later profiling must specifically check:

- layout shift while modules load;
- modal/panel insertion without size jumps;
- preserved scroll position;
- stable header/input geometry;
- no flash of unstyled content;
- no blank frame between shell and enhanced state;
- graceful fallback when an optional module fails.

## 6. Runtime measurements pending

The repository audit cannot establish actual browser timings by itself. The following values must be captured from the preview with Chrome DevTools and Playwright:

| Metric | Baseline |
|---|---|
| TTFB | pending live measurement |
| FCP | pending live measurement |
| LCP | pending live measurement |
| INP / first interaction | pending live measurement |
| CLS | pending live measurement |
| Initial request count | pending live measurement |
| Initial transferred bytes | pending live measurement |
| Critical JS execution time | pending live measurement |
| Long tasks | pending live measurement |
| Offline behavior | pending scenario test |
| Slow 3G behavior | pending scenario test |
| Critical JS failure | pending scenario test |
| Optional JS failure | pending scenario test |
| Android WebView startup | pending device test |

## 7. Measurement protocol

The baseline must be reproducible under the same conditions.

### Cold load

- empty browser cache;
- fresh navigation to the preview;
- no extensions affecting the page;
- record Network and Performance traces.

### Warm load

- reload with populated cache;
- record differences from cold load.

### Slow network

- emulate Slow 3G;
- repeat cold and warm loads;
- record first usable render and first interaction.

### Failure cases

Test independently:

- API unavailable;
- one optional script returns an error;
- critical JS fails;
- stale cache;
- offline after initial shell;
- intermittent connection.

### Mobile/WebView

Repeat the critical scenarios in the Android WebView environment used by the application.

## 8. Target architecture for the next steps

The later implementation should converge toward:

```text
HTML shell
  ↓
critical CSS
  ↓
minimal bootstrap
  ↓
first usable chat
  ↓
optional enhancements
  ├─ settings
  ├─ file manager
  ├─ treasury
  ├─ departments
  ├─ trace viewer
  ├─ voice
  └─ diagnostics/debug
```

The transition between these states must be visually continuous. No enhancement is allowed to visibly destabilize the already usable shell.

## 9. Baseline conclusion

The largest immediately visible repository-level optimization opportunity is not a single slow function. It is the broad initial resource graph: 23 deferred scripts, inline startup code, and multiple feature modules entering the page at once.

The next implementation step should therefore be driven by measured browser timings and dependency analysis, not by simply concatenating files or blindly minifying everything.

## 10. Definition of done for Step 1

- [x] Baseline commit recorded.
- [x] Frontend entrypoint identified.
- [x] Initial HTML/CSS/JS resource graph recorded.
- [x] Static byte sizes recorded.
- [x] Critical/optional candidates identified.
- [x] Existing preview pipeline identified.
- [x] Visual continuity requirements recorded.
- [ ] Live DevTools timing capture.
- [ ] Playwright network/failure capture.
- [ ] Android WebView capture.

The final three items require executing the application in a real browser/device environment and are intentionally not fabricated.
