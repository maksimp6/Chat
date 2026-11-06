# Logos frontend status and transport

The page's scripts share `window.LogosCoreAPI`, defined in `static/core_api.js`, and send their requests through `window.LogosDispatcher`, defined in `static/dispatcher.js`.

## User-visible system status

Scripts publish current state through `LogosCoreAPI.status`. The UI aggregates these states into the system-status indicator, similar to a vehicle dashboard.

Allowed states:

`ready`, `running`, `waiting`, `degraded`, `blocked`, `error`, `stopped`.

A script reports its name, a state and a short human-readable message. The UI decides presentation.

```js
window.LogosCoreAPI.status.set("core", "ready", "Ядро готово");
```

Three names report today: `core`, `network` and `ui`. A script does not manipulate the status panel directly. `status.subscribe` and `status.snapshot` read the current states; `static/system_status.js` draws them.

Messages and metadata pass through redaction before they are stored: keys matching credential-like names become `[REDACTED]`, and so do credential-like pairs inside a string.

## Transport boundary

Application requests go through the Dispatcher, not `fetch`.

```text
Script
  |
  v
Dispatcher
  |
  v
Network
```

`LogosDispatcher.request(input, init, options)` applies the time limit and publishes live network status under the name `network`. `LogosDispatcher.adopt(pending, abort, options)` takes over a request the page started before its scripts ran. `LogosDispatcher.hold()` keeps the page busy for work that outlives its request, and `LogosDispatcher.isBusy()` tells the update check to wait.

Three requests use `fetch` directly: the first history read in `templates/index.html`, which starts ahead of the scripts, the version check in `static/boot.js`, and the `keepalive` delete in `static/file_manager.js`, which runs while the page goes away.

## Validation

Only real Redmi 9 checks remain. See
[Redmi 9 checks](../architecture/browser-self-testing.md) for the device
scenarios, evidence requirements and explicit-run policy. The former static,
VM and BrowserShim test commands have been removed.
