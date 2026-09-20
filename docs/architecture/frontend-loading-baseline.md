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

## 6. Runtime measurements

### 6.1 Manual browser measurements

Measurements were captured against the deployed PR preview with browser DevTools. They are manual, approximate observations and are not treated as a laboratory benchmark.

| Metric | Cold reload, cache disabled | Slow 3G |
|---|---:|---:|
| Requests | 27 | 29 |
| Transferred | 243,695 B (~237.9 KiB) | 246,629 B (~240.8 KiB) |
| First visual content | not measured | ~0.9 s |
| First usable UI | not measured | ~5.3 s |
| Message input visible | not measured | ~5.3 s |
| DOMContentLoaded | not measured | ~5.7 s |
| Network almost idle | not measured | ~5.8 s |
| JavaScript errors | none observed | none observed |
| HTTP 4xx/5xx | none observed | none observed |
| Visible layout shift | not numerically measured | none observed |
| Horizontal overflow | none observed | none observed |

The earlier ~18 KB Network observation was incomplete. The cold-load measurement above supersedes it for the runtime baseline.

### 6.2 No-JS observation

With JavaScript disabled, the server-rendered HTML shell remains visible: the main interface, header/buttons, and message input are present and the page is not blank.

Full form submission and navigation without JavaScript were not separately validated.

### 6.3 Remaining runtime measurements

| Metric / scenario | Baseline |
|---|---|
| TTFB | pending numeric capture |
| FCP | ~0.9 s on manual Slow 3G observation |
| LCP | pending numeric capture |
| INP / first interaction | pending numeric capture |
| CLS | pending numeric capture; no visible shift observed |
| Initial request count/bytes | cold: 27 / 243,695 B; Slow 3G: 29 / 246,629 B |
| Critical JS execution/long tasks | pending Performance trace |
| Offline behavior | pending scenario test |
| 10 KiB/s + 2500 ms latency profile | runner support added; live measurement pending |
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

Cold and warm loads, Slow 3G, a very-low-throughput mobile profile, API/asset failures, stale cache, offline transitions, and Android WebView startup must be tested against the preview.

For the severe low-throughput profile, the runner supports 10 KiB/s download, **0.065 KiB/s upload**, and 2500 ms additional latency:

```powershell
py tests/frontend_baseline.py --base-url "http://88.218.66.166/preview/pr-228/" --download-kbps 10 --upload-kbps 0.065 --latency-ms 2500 --timeout-ms 180000
```

The upload value is calculated by dividing 0.26 KiB/s by 2 twice: `0.26 / 2 / 2 = 0.065 KiB/s`. This profile is intentionally severe and opt-in; it does not change normal baseline defaults. The live measurement remains pending.

The automated probe does not by itself complete Android WebView validation, failure injection, or real-device network validation.

## 9. Definition of done for Step 1

- [x] Baseline commit, entrypoint, resource graph, byte sizes, candidates, and preview pipeline recorded.
- [x] Windows setup and runner documented.
- [x] Manual DevTools cold-load and Slow 3G measurements recorded.
- [ ] Playwright execution against a real preview.
- [ ] 10 KiB/s + 0.065 KiB/s upload + 2500 ms latency live measurement.
- [ ] Failure-injection scenarios.
- [ ] Performance trace / long-task capture.
- [ ] Android WebView capture.

The remaining items require execution in a real browser/device environment and are not fabricated.
