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
