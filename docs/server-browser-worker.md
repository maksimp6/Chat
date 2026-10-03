# Server browser worker — #409

This first vertical slice installs a real Chromium browser on the existing VM
through the same `production` environment and `PREVIEW_SSH_*` secrets as the old
preview deployment. It does not replace Alice, Traefik, or the preview network.
No ports are published. SSH and `docker exec` are the authenticated transport;
Chromedriver and the worker HTTP listener bind only inside the container.

Run **Server browser worker** from protected `master`, first `preflight`, then
`install`, then `navigate` with a credential-free public URL. Installation refuses
to replace an existing worker. The source/image is identified by the exact
checkout SHA. Do not use a PR deployment with production secrets.

The worker keeps one browser alive between commands, serializes requests, and
uses a dedicated persistent `/data/profile`. A restart resets the active page;
the profile remains on the VM. This is not evidence that every site's login will
survive a crash. Actual login and a secure human handoff are future work.

The browser runs as uid 10001, with its sandbox enabled. An egress proxy rejects
private/loopback/link-local destinations and connects to the checked public IP,
including redirects and subresources. QUIC and non-proxied WebRTC UDP are disabled.
The Chromium version comes from Debian's security repository at build time; record
the deployed image ID for reproducibility. The source Dockerfile is not a pinned
binary artifact. Sandbox support must be verified on the actual host; do not add
`--no-sandbox` to make a failed deployment pass.

Current operations: navigate, inspect (title/origin), assert_state (exact title).
The adapter rejects click/fill/screenshot until an executor-bound approval and
sensitive-page output channel are implemented. It does not yet solve the full
"do actions on a website" objective, and #409 must remain open.

`ServerBrowserAdapter(transport)` plugs into `BrowserAdapterRegistry` as
`browser_cloud`. Supply a runtime-scoped transport that sends JSON on stdin to:

```
docker exec -i alice-browser-worker python -m browser.server_worker request
```

Register via the existing `register_browser_tools` and call through
`UniversalToolExecutor`; it owns InvocationContext and ExecutionTrace. No
unconditional app registration or new public MCP endpoint is added. The standalone
Actions smoke result is not an Alice chat ExecutionTrace or proof of ChatGPT MCP
connectivity.

CI tests use a fake W3C driver and deterministic egress checks, never a real
browser or user profile. Live acceptance still requires SSH preflight, image build,
browser startup, navigation result, two consecutive commands, and restart/profile
checks on the VM. An Access Blocked title is a site outcome, not success at login.

Never print keys, cookies, form values or driver exceptions. This initial slice
returns only public-page title and origin. Do not use it on sensitive/authenticated
pages until the output channel supports proper redaction and owner authorization.
