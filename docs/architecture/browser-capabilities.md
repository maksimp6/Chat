# Browser capability contracts

Issue [#409](https://github.com/maksimp6/Chat/issues/409) defines two separate
capabilities for Alice:

- `browser_cloud` uses an explicitly approved cloud session;
- `browser_local` uses the project Browser Emulator/BrowserShim scope and never
  the user's real browser profile.

The contract layer lives in `browser_capabilities.py`. It does not contain a
browser driver. Adapters must expose their tool through `UniversalToolExecutor`
and preserve `InvocationContext`/`ExecutionTrace` correlation.

Both capabilities use the same action vocabulary: `navigate`, `inspect`,
`screenshot`, `click`, `fill` and `assert_state`. Adapter implementations may
support a subset, but must reject unsupported actions before execution.

Cloud and local sessions have different scopes. A local emulator must not gain
cloud credentials, cookies or access to the user's browser profile. A cloud
adapter must not silently fall back to local device access.

Results and errors must pass through `sanitize_browser_value` before entering
traces, logs, issue comments or model-visible output. Values under keys such as
authorization, cookie, password, token, OTP, session and credential are
redacted.

This is the contract/test slice only. The remaining work in #409 is adapter
execution, self-test orchestration, approval UX, and CI smoke coverage. No
Playwright dependency is permitted.
