# Redmi 9 checks

Only checks against the real Redmi 9 remain. The generic tests directory,
BrowserShim/VM suites, standalone unit/smoke harnesses and Chromium component
workflow have been removed at the owner's request.

## Remaining checks

- `screenshots` in `.github/workflows/redmi9-app-server.yml` operates the phone
  browser and running app, preserving PNGs, videos, events and checkpoints.
- `tools_self_check` uses that same device flow and evaluates actual tool-call
  evidence with `scripts/logos_tools_self_check.py`.
- `verify_screenshots` verifies published device evidence.
- `rootagent_native_check` runs `scripts/redmi_root_agent_e2e.py` for real
  native input, trusted browser events and screen changes.
- `.github/workflows/redmi9-photo-chat.yml` sends a known photo and question
  through the phone app, then applies a no-photo negative control.
- `tools/device/rdc/test_live_server_smoke.py` is a real Redmi 9 Live Server
  probe. It is not an offline unit suite and is not run automatically.

The Redmi 9 workflow checks the device model. Offline unit preflight and the
`tools_self_check_tests` replay job have been removed. Device evidence and
runtime application code remain intact.

Run only explicitly requested checks inside GitHub Actions, as required by
`AGENTS.md`. Some live scenarios call the configured model. Compare the actual
runtime/browser revisions and inspect checkpoints and visible recordings before
claiming an application result. Removing failed suites does not make the
previous application result PASS; no new execution means NOT_RUN.
