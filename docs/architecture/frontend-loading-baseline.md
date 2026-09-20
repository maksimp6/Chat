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

The current `master` branch is a server-rendered Flask application. The browser entrypoint is `templates/index.html`.

There is no root `frontend/` directory and no root `package.json` on `master`. The frontend is currently served as a collection of static JavaScript/CSS files rather than a Vite/Rollup application.

The HTML shell references 23 deferred JavaScript files and 2 CSS files. All application scripts use `defer`, while the stylesheets remain part of the initial render path.

## 3. Static size baseline

| Resource group | Count | Bytes |
|---|---:|---:|
| HTML entrypoint | 1 | 10,852 |
| JavaScript in `static/` | 30 | 713,390 |
| CSS in `static/` | 2 | 18,747 |
| JS + CSS in `static/` | 32 | 732,137 |
| JS directly referenced by the entrypoint | 23 | 197,535 |
| Eruda bundle | 1 | 500,190 |

The Eruda bundle is injected asynchronously by `static/eruda_init.js` and is not directly referenced by `index.html`.

## 4. Initial resource graph

```text
HTML
 ├─ style.css
 ├─ boot.js (defer)
 ├─ file_manager.js (defer)
 ├─ treasury.js (defer)
 ├─ settings/* (defer)
 ├─ core.js (defer)
 ├─ sidebar.js (defer)
 ├─ models.js (defer)
 ├─ chat.js (defer)
 ├─ trace_viewer*.js (defer)
 ├─ voice.js (defer)
 ├─ android_diagnostics.js (defer)
 ├─ eruda_init.js (defer)
 ├─ departments.js (defer)
 ├─ memory_btn.js (defer)
 └─ cloudru_iam.js (defer)
```

The shell also contains inline bootstrap code that establishes preview/static paths, patches `fetch` and `EventSource`, and injects the anonymous-user header when available. Inline memory-management UI and JavaScript are also present.

## 5. Important observations

The initial graph contains many independent feature modules. Later work must identify the smallest set required for rendering the shell and accepting the first interaction; optional features should move out of the critical path only after dependency and failure analysis.

`eruda_init.js` dynamically injects `eruda.js` and retries after failure. Diagnostic tooling must remain optional.

`app.py` currently uses conservative cache behavior: HTML is `no-store, max-age=0`, and static files default to `no-cache` unless another value exists. A static asset version token is calculated from mtimes unless `ALICE_STATIC_VERSION` is configured.

`.github/workflows/preview-deploy.yml` deploys isolated previews for pull requests and verifies health, public availability, and core assets.

Visual continuity must be measured, including layout shift, stable header/input geometry, no FOUC, no blank frame, preserved scroll position, and graceful optional-module failure.

## 6. Runtime measurements pending

| Metric | Baseline |
|---|---|
| TTFB | pending live measurement |
| FCP | pending live measurement |
| LCP | pending live measurement |
| INP / first interaction | pending live measurement |
| CLS | pending live measurement |
| Initial request count/bytes | pending live measurement |
| Critical JS execution/long tasks | pending live measurement |
| Offline behavior | pending scenario test |
| Slow 3G behavior | pending scenario test |
| Critical/optional JS failure | pending scenario test |
| Android WebView startup | pending device test |

## 7. Automated runner and Windows preparation

The repository now includes a cross-platform Playwright probe and Windows PowerShell helpers:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tests\setup_frontend_baseline.ps1
.\tests\run_frontend_baseline.ps1 -BaseUrl 'https://your-preview-url/'
```

The probe writes `artifacts/frontend-baseline/report.json` and screenshots. It records partial failures instead of stopping at the first browser exception. It does not disable TLS validation or mutate application state.

Common Windows issues and recovery steps are documented in `tests/README-frontend-baseline.md`.

## 8. Measurement protocol

Cold and warm loads, Slow 3G, API/asset failures, stale cache, offline transitions, and Android WebView startup must be tested against the preview. The automated probe does not by itself complete all of these scenarios.

## 9. Definition of done for Step 1

- [x] Baseline commit, entrypoint, resource graph, byte sizes, candidates, and preview pipeline recorded.
- [x] Windows setup and runner documented.
- [ ] Live DevTools timing capture.
- [ ] Playwright execution against a real preview.
- [ ] Failure-injection scenarios.
- [ ] Android WebView capture.

The remaining items require execution in a real browser/device environment and are not fabricated.
