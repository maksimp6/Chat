# Frontend Loading Baseline

**Issue:** #227  
**Baseline ref:** `master`  
**Baseline commit:** `d4ad1b154f7822f48e613646bcdff7a94285c13d`  
**Audit type:** repository/static loading audit  
**Date:** 2026-09-20

## 1. Scope

This baseline describes the current browser entrypoint and the resources referenced by the main HTML shell. It is the reference point for later performance changes.

The audit intentionally does not modify the existing application shell. Runtime browser measurements are treated as evidence only when they include the exact network profile, timing method, and known limitations.

Two network profiles must not be conflated:

- **Observed manual test:** 10 KB/s download, 10 KB/s upload, 200 ms latency.
- **Automated severe stress test:** 10 KiB/s download, 0.065 KiB/s upload, 2500 ms additional latency.

The distinction between **KB** and **KiB** is intentional. The automated runner accepts throughput in KiB/s and converts it to bytes/s using 1024 bytes per KiB.

An observation that a browser looked blank, or that DevTools appeared to show an empty DOM, is not by itself proof that the server failed to deliver the HTML. Root-cause work must distinguish transport, parser, CSS/visibility, inline bootstrap, deferred script execution, and application initialization.

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

### 6.2 Additional very-slow-network observation

A manual DevTools CDP test was performed with:

- Download: 10 KB/s
- Upload: 10 KB/s
- Latency: 200 ms

The first 3 seconds showed a blank DOM/screen in the observation. The HTML shell was not visibly available during that interval. The page eventually loaded and the final UI was usable, but request count, transferred bytes, DOMContentLoaded, first usable render, and network-idle timing were not captured. No JavaScript or HTTP errors were observed.

This is an observed finding, not a complete benchmark. It indicates that the early-render path requires investigation under extreme network constraints.

### 6.3 No-JS observation

With JavaScript disabled, the server-rendered HTML shell remains visible: the main interface, header/buttons, and message input are present and the page is not blank.

Full form submission and navigation without JavaScript were not separately validated.

### 6.4 Automated probe: early-render instrumentation

The Playwright probe now uses navigation `wait_until="commit"` before collecting early milestones. This is a deliberate correction to the earlier implementation, which waited for `DOMContentLoaded` first and could miss the exact failure window under severe throttling.

After commit, the probe records milestones at approximately:

- 250 ms
- 1 s
- 3 s
- 5 s

Each milestone captures:

- document `readyState`;
- presence of `html` and `body`;
- body child count and body text size;
- presence and visual visibility of `#app-root`;
- presence and visual visibility of `#msg-input`;
- whether the shell satisfies a conservative first-usable candidate condition.

The report also records:

- `blank_dom_observed`: at least one sampled milestone had no visible `#app-root` and zero body text bytes;
- `first_usable_milestone_ms`: first sampled milestone where both the shell and message input were visible;
- main-response status and commit time;
- resource count and transfer bytes;
- failed requests and HTTP responses with status >= 400;
- console/page errors;
- FCP/paint and navigation timings.

`blank_dom_observed` is intentionally a DOM-level diagnostic signal. It is not a substitute for a screenshot or Performance trace when determining the visual root cause.

### 6.5 Automated severe-profile measurement

The severe-profile Playwright probe was executed against `http://88.218.66.166/preview/pr-228/` with:

- Download: 10 KiB/s
- Upload: 0.065 KiB/s
- Additional latency: 2500 ms
- Timeout: 180000 ms
- Trace capture: enabled

Observed result:

| Metric | Value |
|---|---:|
| Main response status | 200 |
| Commit | 5447.7 ms |
| DOMContentLoaded | 32984.3 ms |
| First paint / FCP | 10540 ms |
| Resource count | 25 |
| Resource transfer bytes | 223,782 B |
| Console/page errors | 0 |
| Failed requests / HTTP >= 400 | 0 |
| `blank_dom_observed` | true |
| `first_usable_milestone_ms` | null |

The early milestone samples targeted 250 ms, 1 s, 3 s, and 5 s after navigation start, but the main response did not commit until 5447.7 ms. All four recorded milestone samples therefore occurred immediately after commit, while the document was still `loading`; they had no visible `#app-root`, no visible `#msg-input`, and zero body text bytes.

The same run confirmed that the no-JS shell remains present: HTTP 200, non-empty body, and `#app-root` present. The trace archive was captured as `artifacts/frontend-baseline-severe/trace.zip` in the local run artifacts.

This result confirms the severe-profile early blank DOM condition. It still does not identify the root cause by itself; the next step is trace/resource-timing analysis across transport, parser, CSS visibility, inline bootstrap, deferred scripts, and app initialization.

### 6.6 Remaining runtime measurements

| Metric / scenario | Status |
|---|---|
| TTFB | pending numeric capture |
| FCP | ~0.9 s on manual Slow 3G observation; 10540 ms on automated severe profile |
| LCP | pending numeric capture |
| INP / first interaction | pending numeric capture |
| CLS | pending numeric capture; no visible shift observed |
| Initial request count/bytes | cold: 27 / 243,695 B; Slow 3G: 29 / 246,629 B |
| Early 250 ms / 1 s / 3 s / 5 s milestones | captured for severe profile; all sampled after commit and blank |
| Critical JS execution / long tasks | trace captured for severe profile; analysis pending |
| Offline behavior | runner smoke check exists; interpretation/coverage pending |
| 10 KiB/s + 0.065 KiB/s upload + 2500 ms latency | live measurement captured |
| Critical/optional JS failure | pending scenario test |
| Stale-cache behavior | pending scenario test |
| API timeout / 500 behavior | pending scenario test |
| Android WebView startup | pending device test |


## 7. Automated runner and Windows preparation

The repository includes a cross-platform Playwright probe and Windows PowerShell helpers.

Basic Windows run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tests\setup_frontend_baseline.ps1
.\tests\run_frontend_baseline.ps1 -BaseUrl 'https://your-preview-url/'
```

The Windows runner accepts the same network controls as the Python probe:

```powershell
.\tests\run_frontend_baseline.ps1 -BaseUrl 'http://88.218.66.166/preview/pr-228/' -DownloadKbps 10 -UploadKbps 0.065 -LatencyMs 2500 -TimeoutMs 180000 -Trace
```

The probe writes `artifacts/frontend-baseline/report.json` and screenshots. When `--trace` is supplied, it also writes `artifacts/frontend-baseline/trace.zip`.

The report records partial failures instead of stopping at the first browser exception. It does not disable TLS validation or mutate application state.

Common Windows issues and recovery steps are documented in `tests/README-frontend-baseline.md`.


## 8. Measurement protocol

### 8.1 Normal baseline

Capture at minimum:

1. cold reload with cache disabled;
2. warm reload;
3. Slow 3G;
4. no-JS load;
5. offline load.

Record exact request count, transferred bytes, TTFB, FCP, LCP, DOMContentLoaded, first usable interaction, errors, and layout stability where tooling permits.

### 8.2 Severe network reproduction

For the severe stress profile:

```powershell
py tests/frontend_baseline.py --base-url "http://88.218.66.166/preview/pr-228/" --download-kbps 10 --upload-kbps 0.065 --latency-ms 2500 --timeout-ms 180000 --trace
```

The upload value is calculated as:

```text
0.26 / 2 / 2 = 0.065 KiB/s
```

This profile is intentionally severe and opt-in. It must not replace the normal baseline.

### 8.3 Root-cause evidence gate

If the 3-second or earlier milestone still reports a blank DOM/screen:

1. keep application code unchanged;
2. inspect `early_milestones` in `report.json`;
3. inspect the screenshot and, when enabled, `trace.zip`;
4. compare commit time, response start, FCP, resource timing, failed requests, and first visible shell state;
5. inspect `templates/index.html`, critical CSS, inline bootstrap, and first deferred scripts;
6. only then decide whether a code change is justified.

Do not label the cause as “server did not send HTML” unless transport evidence supports it.

### 8.4 Failure-injection matrix

| Scenario | Expected property |
|---|---|
| Critical CSS delayed/failed | server HTML remains structurally available and recovery is deterministic |
| One optional JS module fails | core interface remains usable |
| API returns 500 | visible bounded error state; no global UI collapse |
| API request times out | retry/recovery path; no permanent loading state |
| Stale cache | old/new asset mismatch does not create a dead shell |
| Offline transition | existing UI remains coherent and recovery is possible |
| JS disabled | server-rendered shell remains useful |
| Android WebView pause/resume | initialization remains idempotent |

These are architecture tests, not merely CI-green requirements.


## 9. Definition of done for Step 1

- [x] Baseline commit, entrypoint, resource graph, byte sizes, candidates, and preview pipeline recorded.
- [x] Windows setup and runner documented.
- [x] Manual DevTools cold-load and Slow 3G measurements recorded.
- [x] Extreme-network blank-screen observation recorded with its actual parameters and limitations.
- [x] Runner accepts KiB/s download/upload and latency controls.
- [x] Runner captures early post-commit milestones before waiting for DOMContentLoaded.
- [x] Runner records explicit DOM-level blank and first-usable signals.
- [x] Runner can optionally capture a Playwright trace.
- [x] Playwright execution against the real PR preview.
- [x] 10 KiB/s + 0.065 KiB/s upload + 2500 ms latency live measurement.
- [ ] Root-cause analysis of the blank-screen observation.
- [ ] Failure-injection scenarios.
- [ ] Stale-cache/API-timeout/API-500 scenarios.
- [ ] Android WebView capture.
- [ ] Final normal cold/Slow 3G regression after the root cause is addressed.

The remaining items require execution in a real browser/device environment and are not fabricated.
