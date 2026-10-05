# Browser adapter boundary

The second implementation slice of [#409](https://github.com/maksimp6/Chat/issues/409)
adds `BrowserAdapterRegistry` and an explicit `register_browser_tools` opt-in.

An adapter must implement one method:

```python
execute(action: BrowserAction) -> Mapping[str, Any]
```

The registry:

1. validates the capability and action;
2. returns a clear `adapter_unavailable` result when no backend is registered;
3. classifies adapter exceptions and malformed results;
4. sanitizes data and metadata before returning them;
5. can register both browser contracts with `ToolRegistry`, so calls still pass
   through `UniversalToolExecutor`.

Registration is intentionally opt-in. Adding a contract does not grant access
to a cloud session, device browser, cookies or credentials. The next slice can
implement a BrowserShim-backed local adapter and a separately approved cloud
adapter without changing the dispatcher boundary.

## Deterministic browser emulator

`browser/emulator/` contains the lightweight BrowserShim-based emulator used by
frontend tests and deterministic local browser workflows. It supports
`navigate`, `inspect`, bounded link/form `click`, `fill`, and
`assert_state`.

The emulator does not execute remote page scripts, does not provide screenshots,
and does not claim a persistent real-browser profile, cookies, extensions, or
account session.

`browser.emulator_adapter.EmulatorBrowserAdapter` may be registered explicitly
for `browser_local`:

```python
registry = BrowserAdapterRegistry({"browser_local": EmulatorBrowserAdapter()})
```

`browser_cloud` remains a separate real-browser capability. A healthy emulator
must not be used as evidence that Chrome/Playwright, JavaScript rendering, a real
profile, or cloud-browser control works.

