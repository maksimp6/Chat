# BrowserShim self-testing

Issue [#409](https://github.com/maksimp6/Chat/issues/409) now has a local,
deterministic self-test runner: `tests/browser_self_test_runner.js`.

It runs only supplied in-memory HTML fixtures using the repository's
`BrowserShim`. It has no network client, does not load a browser profile and
does not evaluate scenario-provided JavaScript.

Supported steps are:

- `navigate` to a named fixture;
- `inspect` a selector;
- `fill` a selector with a string;
- `click` a selector;
- `assert_state` with a JSON-object expectation.

Every run returns a structured report with successful steps or the first
detected failure. Known blocked cases include missing fixtures, missing
selectors, unsupported screenshots, invalid input and failed assertions. This
lets Alice attach deterministic self-test evidence before she reports a browser
workflow as complete.

The runner is intentionally a local-emulator component. It does not implement
cloud browsing, real screenshots, arbitrary web navigation, cookie access or
automatic submission of sensitive actions. Those capabilities remain behind
their explicit adapter and approval boundaries.
