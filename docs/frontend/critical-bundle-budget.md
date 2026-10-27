# Critical bundle budget

Issue: #223
Baseline commit: 4c718ea
Measurement date: 2026-09-28

This documents the methodology and the current, measured answer to "can the
web app's minimally-working version fit in 50 KB without pretending to be
more functional than it is." It does not repeat the broader progressive
enhancement work tracked by #227; this is scoped to bundle size only.

## What "critical" means here

The repository already has a critical-vs-optional script contract, enforced
by `tests/test_frontend_script_contract.py`: `templates/index.html` loads a
synchronous `id="alice-boot"` script first, then a chain of scripts marked
`data-critical-script="core-api|ui-runtime|dispatcher|core"`, before any
other application module. That chain is the fetch/dispatcher/startup glue
the shell needs to render and to make a request at all. Everything else
(`static/chat.js`, `static/sidebar.js`, `static/models.js`, the settings
modules, trace viewer, voice, treasury, file manager, MCP/departments/
environments UI, etc.) is not part of that chain today.

`scripts/measure_critical_bundle.py` derives the **critical set** from those
same markers instead of a hand-maintained file list, so this budget and the
script contract test cannot silently drift apart:

- `templates/index.html` — the shell markup itself;
- the first `<link rel="stylesheet">` in `<head>` — `static/style.css`;
- the synchronous `id="alice-boot"` script — `static/boot.js`;
- every script carrying `data-critical-script="..."` — currently
  `core_api.js`, `ui_runtime.js`, `dispatcher.js`, `core.js`.

The script also reports a **full page** total: every local script/stylesheet
`templates/index.html` references, critical or not. That second number is
the "measure the full build separately" figure the issue asks for, so the
critical-path budget and the whole-page weight are never accidentally mixed.

## Raw vs gzip rule

Raw bytes on disk are the authoritative number for this budget: `app.py`
does not enable HTTP compression and no gzip/Brotli step exists in
`deploy/`, so raw bytes are what a client actually downloads today. Gzip
size (Python's `gzip` module, `compresslevel=9`, each file compressed
independently, matching how a compressing server would encode separate HTTP
responses) is reported by the script for reference only, in case compression
is added at a reverse proxy later — it is not used to decide pass/fail.

`templates/index.html` is measured as the template file on disk, not a
Jinja-rendered response. The only runtime substitutions are the
`static_version` cache-busting query parameter and, on preview deployments,
a `preview_base_path` prefix; both add at most a few dozen bytes and do not
change the conclusion below.

## Measured result (2026-09-28, commit 4c718ea)

| File | Raw bytes |
|---|---:|
| `templates/index.html` | 15,961 |
| `static/style.css` | 36,036 |
| `static/boot.js` | 6,404 |
| `static/core_api.js` | 17,907 |
| `static/ui_runtime.js` | 560 |
| `static/dispatcher.js` | 2,746 |
| `static/core.js` | 11,451 |
| **Critical set total** | **91,065 (≈88.9 KiB)** |

Full page (every local script/stylesheet the shell references, critical or
not): **334,259 bytes (≈326.4 KiB)**, i.e. the ~243 KB outside the critical
set is `chat.js`, `sidebar.js`, `models.js`, `settings*.js`, the trace
viewer, voice, treasury, file manager, departments/environments/partner
relations, memory panel, provider credentials, and similar modules — the
"full chat, settings, model management, MCP, tracing" functionality the
issue already expected would not fit in a minimal budget.

Run `python3 scripts/measure_critical_bundle.py` for the full per-file
report including gzip sizes, or `--check` to run the CI gate described
below.

## Conclusion: 50 KB is not reachable today

The critical set alone is **91,065 bytes raw, ~78% over the 50 KB (51,200
byte) target**, before any chat, sidebar, model-selection, or settings
functionality is added. It is already minimal by the repository's own
existing contract — nothing "non-essential" is loaded ahead of first render
today, so there is no script to move later as a small change. The overshoot
comes from three things that cannot be trimmed without a real redesign:

- `static/style.css` (36,036 B) is the single largest contributor and is
  not split into a critical/deferred subset;
- `static/core_api.js` (17,907 B) is the fetch/response-handling layer the
  shell needs to make any request, not something that can be deferred past
  first interaction;
- `templates/index.html` (15,961 B) is the server-rendered shell markup
  itself (header, sidebar skeleton, modals, no-JS fallback), not a script.

Reaching 50 KB would require splitting `style.css` into a small
first-paint subset plus a deferred stylesheet, and/or trimming
`core_api.js`/`core.js` to a smaller shell-only surface with the rest loaded
on demand. Both are exactly the kind of broader frontend restructuring
already tracked by #227, not a small, independently reviewable change, so
they are out of scope for this PR. **The 50 KB target is not met and should
be treated as aspirational until #227-scale work lands**, or formally
revised.

## Revised CI budget

Since 50 KB is not reachable today, CI enforces a **regression** budget
instead of the aspirational target: **98,304 bytes (96 KiB) raw** for the
critical set, defined as `DEFAULT_LIMIT_BYTES` in
`scripts/measure_critical_bundle.py`. That is the measured 91,065 B plus
headroom for incidental growth (version query strings, small copy changes),
while still failing the build if the critical set grows materially — for
example if a future change adds another script to the critical chain, or
`style.css`/`core_api.js` grow without a matching justification here.
`tests/test_critical_bundle_budget.py` runs `scripts/measure_critical_bundle.py
--check` as part of the existing Python test suite (`pytest -q`, already run
in CI), so no `.github/workflows` change was needed to add this gate.

If the critical set shrinks in a future change (e.g. after a `style.css`
split), lower `DEFAULT_LIMIT_BYTES` in the same change and update the
measured numbers in this document — do not let the budget drift away from
reality in either direction.

## What does not work until the deferred scripts finish loading

Everything outside the critical set is requested with `defer` during the
same initial page load, in parallel with the critical scripts — this
codebase does not yet implement real on-demand/lazy loading (fetching a
module only after the user interacts with something), only the existing
critical/optional split that governs blocking behavior and failure
isolation. So there is no window today where the shell is visible but
network-idle while waiting for these; they load concurrently with the
critical set. Implementing genuine on-demand loading for the ~30 modules
below is a substantial change and is tracked under #227, not this issue.

What that means honestly for a constrained connection where the initial
HTML/CSS/critical-script chain (~89 KiB) has arrived but the remaining
~243 KiB of deferred scripts have not:

- **Sending a message does not work yet.** The send button, its handler,
  and response rendering live in `static/chat.js`, which is not in the
  critical set. Until it loads, clicking send does nothing.
- **The conversation sidebar is inert.** Populating and navigating
  `#conv-list` is `static/sidebar.js`.
- **Model selection is unavailable.** The model modal is populated and
  wired by `static/models.js`.
- **Settings, MCP configuration, provider credentials, SSH runtime, file
  manager, project tree, treasury, departments, environments, partner
  relations, memory panel, trace viewer, voice input, and dozzle logs** are
  each owned by their own deferred module (`settings.js` and
  `static/settings/*.js`, `provider_credentials.js`,
  `settings/ssh_runtime_modal.js`, `file_manager.js`, `project_tree.js`,
  `treasury.js`, `departments.js`, `environments.js`,
  `partner_relations.js`, `memory_panel.js`, `trace_viewer*.js`, `voice.js`,
  `dozzle.js`) and are unavailable until that module loads.
- **Most header buttons do nothing** until `header_actions.js` (also
  deferred) registers their handlers.
- `android_diagnostics.js` and `eruda_init.js` are already explicitly
  marked `data-optional-script="true"` and are debug/diagnostic tooling,
  not user-facing functionality — their absence is expected and already
  covered by `tests/test_frontend_optional_scripts.py`.

In practice, on any connection fast enough to load ~89 KiB at all, the
remaining ~243 KiB of deferred scripts finish within the same page load and
the gap above is not user-visible. On the deliberately extreme profile this
issue is motivated by (see `docs/frontend/progressive-enhancement-audit.md`,
10 KiB/s down), the shell would render long before the app becomes
interactive, and every item above would be genuinely broken for that
window — this is not the "illusion of a working app" the issue warns
against, because `#frontend-degraded-status` and the `<noscript>` fallback
already exist in the shell for exactly this case, and no critical-path
change in this PR claims otherwise.
